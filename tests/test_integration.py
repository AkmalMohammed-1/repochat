"""Index the tiny fixture repo into a real Postgres + pgvector and query it. Needs a database:
docker compose up -d db   (tests are skipped otherwise)."""

from pathlib import Path

import pytest

from api.core import snapshots
from api.core.embeddings import HashEmbedder
from api.graph.retrieval import RetrievalConfig, retrieve
from worker.pipeline.run import index_snapshot

pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parent / "fixtures" / "tiny_repo"


async def _count(pool, snapshot_id: int) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM chunks WHERE snapshot_id = %s", (snapshot_id,))
        return (await cur.fetchone())["n"]


async def test_index_and_hybrid_search(pool):
    embedder = HashEmbedder(384)
    repo_id = await snapshots.upsert_repo(pool, "test", "tiny_repo", "main")
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM snapshots WHERE repo_id = %s", (repo_id,))
    snapshot_id = await snapshots.ensure_indexed(pool, repo_id, "fixture", "ast_header", enqueue=False)

    await index_snapshot(pool, snapshot_id, embedder=embedder, source_dir=FIXTURE, summarize=False)
    snap = await snapshots.get_snapshot(pool, snapshot_id)
    assert snap["status"] == "ready"
    assert snap["files_total"] == snap["files_done"] == 7
    n = await _count(pool, snapshot_id)
    assert n == snap["chunks_done"] > 10

    # Keyword search finds the snake_case function from a camelCase-ish question.
    got = await retrieve(pool, embedder, snapshot_id, "how does processRefund work",
                         ["processRefund"], RetrievalConfig(hybrid=True, rerank=False), top_n=5)
    assert any(c["path"] == "shop/payments.py" and c["symbol_name"] == "PaymentService" for c in got)

    # Focused file is always included.
    got = await retrieve(pool, embedder, snapshot_id, "what is this", [], RetrievalConfig(rerank=False),
                         focus_path="worker/queue.go", top_n=3)
    assert any(c["path"] == "worker/queue.go" for c in got)

    # Idempotent: re-running a finished job is a no-op, and a forced re-run doesn't duplicate chunks.
    await index_snapshot(pool, snapshot_id, embedder=embedder, source_dir=FIXTURE, summarize=False)
    assert await _count(pool, snapshot_id) == n
    async with pool.connection() as conn:
        await conn.execute("UPDATE snapshots SET status = 'failed' WHERE id = %s", (snapshot_id,))
    await index_snapshot(pool, snapshot_id, embedder=embedder, source_dir=FIXTURE, summarize=False)
    assert await _count(pool, snapshot_id) == n

    repo_map = await snapshots.get_repo_map(pool, snapshot_id)
    assert "payments.py" in repo_map["file_tree"] and len(repo_map["paths"]) == 7


async def test_concurrent_resolve_creates_one_snapshot(pool):
    import asyncio

    repo_id = await snapshots.upsert_repo(pool, "test", "race", "main")
    ids = await asyncio.gather(*(snapshots.ensure_indexed(pool, repo_id, "sha-race", "ast_header", enqueue=False)
                                 for _ in range(5)))
    assert len(set(ids)) == 1


class StubLLM:
    """Echoes the first retrieved chunk's citation so the test can check the whole streaming path."""

    async def complete(self, system, messages, max_tokens=512):
        return '{"query": "refund rules", "identifiers": ["process_refund"]}'

    async def stream(self, system, messages, max_tokens=2048):
        import re

        cite = re.search(r'cite="(\[[^"]+\])"', messages[-1]["content"]).group(1)
        for token in ["Refunds ", "are checked ", cite]:
            yield token


async def test_chat_graph_streams_sources_tokens_and_citations(pool):
    from api.graph.citations import resolve_citations
    from api.graph.graph import Deps, build_graph
    from api.graph.retrieval import RetrievalConfig

    embedder = HashEmbedder(384)
    repo_id = await snapshots.upsert_repo(pool, "test", "tiny_chat", "main")
    snapshot_id = await snapshots.ensure_indexed(pool, repo_id, "fixture", "ast_header", enqueue=False)
    await index_snapshot(pool, snapshot_id, embedder=embedder, source_dir=FIXTURE, summarize=False)

    graph = build_graph(Deps(pool=pool, embedder=embedder, llm=StubLLM(), cfg=RetrievalConfig(rerank=False)))
    state = {"question": "and when are they rejected?", "history": [
        {"role": "user", "content": "How do refunds work?"}, {"role": "assistant", "content": "..."}],
        "snapshot": {"id": snapshot_id, "owner": "test", "repo": "tiny_chat", "sha": "fixture"},
        "focus_path": None, "overview": None}
    events = [e async for e in graph.astream(state, stream_mode="custom")]
    assert events[0]["type"] == "sources" and events[0]["chunks"]
    tokens = [e["text"] for e in events if e["type"] == "token"]
    answer = "".join(tokens)
    assert len(tokens) == 3
    cites = resolve_citations(answer, events[0]["chunks"], "test", "tiny_chat", "fixture")
    assert len(cites) == 1 and cites[0].valid


async def test_repo_names_case_insensitive(pool):
    a = await snapshots.upsert_repo(pool, "SomeOrg", "MyRepo", "main")
    b = await snapshots.upsert_repo(pool, "someorg", "myrepo", None)
    assert a == b
    row = await snapshots.find_repo(pool, "SOMEORG", "MYREPO")
    assert row["owner"] == "someorg" and row["default_branch"] == "main"
