"""Hybrid retrieval: pgvector similarity + Postgres full-text, merged with Reciprocal Rank Fusion,
then (optionally) reranked with a cross-encoder."""

import asyncio
import re
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from psycopg_pool import AsyncConnectionPool

from api.core.config import get_settings
from api.core.embeddings import Embedder
from worker.pipeline.chunk import split_identifier

RRF_K = 60  # standard constant from the RRF paper; damps the influence of top ranks


@dataclass(frozen=True)
class RetrievalConfig:
    hybrid: bool = True
    rerank: bool = True


_COLUMNS = "id, path, language, symbol_name, kind, start_line, end_line, content"
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STOP = {
    "the", "and", "for", "are", "how", "what", "where", "which", "does", "this", "that", "with", "from",
    "into", "when", "why", "who", "can", "you", "use", "used", "using", "there", "their", "is", "it", "in",
    "of", "to", "a", "an", "do", "be", "on", "or", "by", "as", "at", "me", "show", "tell", "about", "project",
    "code", "repo", "repository", "file", "files", "function", "work", "works", "i", "we", "my",
}
_IDENT_HINT = re.compile(r"`([^`]+)`|\b([a-z]+[A-Z]\w*|[A-Za-z]+_\w+|[A-Z][a-z]+[A-Z]\w*|\w+\.\w+)\b")


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = RRF_K) -> list[int]:
    """Merge ranked id lists: score(id) = sum over lists of 1 / (k + rank), rank starting at 1."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda i: (-scores[i], i))


def extract_identifiers(question: str) -> list[str]:
    """Likely code identifiers in a question: `backticked`, camelCase, snake_case, dotted.names."""
    found: list[str] = []
    for m in _IDENT_HINT.finditer(question):
        ident = (m.group(1) or m.group(2)).strip()
        if ident and ident not in found:
            found.append(ident)
    return found


def build_tsquery(query: str, identifiers: list[str]) -> str | None:
    """OR-query over meaningful words (keyword search should widen recall, not require every word)."""
    words: set[str] = set()
    for w in _WORD.findall(" ".join([query, *identifiers])):
        lw = w.lower()
        if len(lw) > 2 and lw not in _STOP and "_" not in lw:
            words.add(lw)
        words.update(p for p in split_identifier(w) if len(p) > 2 and p not in _STOP)
    if not words:
        return None
    return " | ".join(sorted(words))


async def vector_search(pool: AsyncConnectionPool, snapshot_id: int, qvec: list[float], k: int,
                        path: str | None = None) -> list[dict]:
    vec = np.asarray(qvec, dtype=np.float32)
    where, params = "snapshot_id = %s", [snapshot_id]
    if path:
        where += " AND path = %s"
        params.append(path)
    async with pool.connection() as conn, conn.transaction():
        # pgvector >= 0.8: keep scanning the HNSW index until k rows pass the snapshot filter.
        await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        cur = await conn.execute(
            f"SELECT {_COLUMNS}, 1 - (embedding <=> %s) AS score FROM chunks WHERE {where} "
            "ORDER BY embedding <=> %s LIMIT %s",
            [vec, *params, vec, k],
        )
        return await cur.fetchall()


async def keyword_search(pool: AsyncConnectionPool, snapshot_id: int, tsquery: str | None, k: int) -> list[dict]:
    if not tsquery:
        return []
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"SELECT {_COLUMNS}, ts_rank_cd(tsv, q) AS score FROM chunks, to_tsquery('simple', %s) q "
            "WHERE snapshot_id = %s AND tsv @@ q ORDER BY score DESC, id LIMIT %s",
            (tsquery, snapshot_id, k),
        )
        return await cur.fetchall()


@lru_cache
def _cross_encoder():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(get_settings().reranker_model, device="cpu")


async def rerank(query: str, chunks: list[dict], top_n: int) -> list[dict]:
    if not chunks:
        return []

    def score() -> list[float]:
        pairs = [(query, f"{c['path']} {c['symbol_name'] or ''}\n{c['content'][:2000]}") for c in chunks]
        return [float(s) for s in _cross_encoder().predict(pairs, show_progress_bar=False)]

    scores = await asyncio.to_thread(score)
    ranked = sorted(zip(scores, chunks, strict=True), key=lambda x: -x[0])
    return [{**c, "score": s} for s, c in ranked[:top_n]]


async def search_candidates(pool: AsyncConnectionPool, embedder: Embedder, snapshot_id: int, query: str,
                            identifiers: list[str], cfg: RetrievalConfig, focus_path: str | None = None,
                            k: int | None = None) -> tuple[list[dict], dict | None]:
    """Vector (+ keyword) search fused with RRF. Returns (candidates, best chunk of focus_path or None)."""
    k = k or get_settings().retrieve_k
    qvec = await embedder.embed_query(" ".join([query, *identifiers]))
    tasks = [vector_search(pool, snapshot_id, qvec, k)]
    if cfg.hybrid:
        tasks.append(keyword_search(pool, snapshot_id, build_tsquery(query, identifiers), k))
    if focus_path:
        tasks.append(vector_search(pool, snapshot_id, qvec, 4, path=focus_path))
    results = await asyncio.gather(*tasks)

    by_id = {row["id"]: row for rows in results for row in rows}
    if len(results) > 1:
        order = reciprocal_rank_fusion([[r["id"] for r in rows] for rows in results])
    else:
        order = [r["id"] for r in results[0]]
    focus = results[-1][0] if focus_path and results[-1] else None
    return [by_id[i] for i in order], focus


async def select_final(query: str, candidates: list[dict], cfg: RetrievalConfig, focus: dict | None = None,
                       top_n: int | None = None) -> list[dict]:
    top_n = top_n or get_settings().final_k
    ranked = await rerank(query, candidates, top_n) if cfg.rerank else candidates[:top_n]
    if focus is not None and not any(c["path"] == focus["path"] for c in ranked):
        ranked = [focus, *ranked[: top_n - 1]]  # always keep the file the user opened
    return ranked


async def retrieve(pool: AsyncConnectionPool, embedder: Embedder, snapshot_id: int, query: str,
                   identifiers: list[str] | None = None, cfg: RetrievalConfig = RetrievalConfig(),
                   focus_path: str | None = None, k: int | None = None, top_n: int | None = None) -> list[dict]:
    """search_candidates + select_final in one call (used by the eval runner and tests)."""
    candidates, focus = await search_candidates(pool, embedder, snapshot_id, query, identifiers or [], cfg,
                                                focus_path, k)
    return await select_final(query, candidates, cfg, focus, top_n)
