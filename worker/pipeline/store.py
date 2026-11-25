"""Postgres writes for the indexing pipeline."""

import numpy as np
from psycopg_pool import AsyncConnectionPool

from worker.pipeline.chunk import Chunk


async def get_snapshot(pool: AsyncConnectionPool, snapshot_id: int) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT s.*, r.owner, r.name AS repo FROM snapshots s JOIN repos r ON r.id = s.repo_id "
            "WHERE s.id = %s", (snapshot_id,))
        return await cur.fetchone()


async def update_snapshot(pool: AsyncConnectionPool, snapshot_id: int, **fields) -> None:
    sets = ", ".join(f"{k} = %({k})s" for k in fields)
    extra = ", finished_at = now()" if fields.get("status") in ("ready", "failed", "rejected") else ""
    async with pool.connection() as conn:
        await conn.execute(f"UPDATE snapshots SET {sets}, updated_at = now(){extra} WHERE id = %(id)s",
                           {**fields, "id": snapshot_id})


async def clear_snapshot_data(pool: AsyncConnectionPool, snapshot_id: int) -> None:
    """Idempotency: a retried job starts from a clean slate instead of adding duplicate chunks."""
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("DELETE FROM chunks WHERE snapshot_id = %s", (snapshot_id,))
        await conn.execute("DELETE FROM repo_maps WHERE snapshot_id = %s", (snapshot_id,))


async def insert_chunks(pool: AsyncConnectionPool, snapshot_id: int, chunks: list[Chunk],
                        embeddings: list[list[float]], search_texts: list[str]) -> None:
    rows = [
        (snapshot_id, c.path, c.language, c.symbol_name, c.kind, c.start_line, c.end_line,
         c.content.replace("\x00", ""), np.asarray(e, dtype=np.float32), t.replace("\x00", "")[:500_000])
        for c, e, t in zip(chunks, embeddings, search_texts, strict=True)
    ]
    async with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO chunks (snapshot_id, path, language, symbol_name, kind, start_line, end_line, content,"
            " embedding, tsv) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, to_tsvector('simple', %s))",
            rows,
        )


async def copy_chunks_forward(pool: AsyncConnectionPool, base_id: int, snapshot_id: int,
                              paths: list[str]) -> int:
    """Incremental re-index: reuse chunks (and embeddings) of files that didn't change."""
    if not paths:
        return 0
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO chunks (snapshot_id, path, language, symbol_name, kind, start_line, end_line, content,"
            " embedding, tsv) SELECT %s, path, language, symbol_name, kind, start_line, end_line, content,"
            " embedding, tsv FROM chunks WHERE snapshot_id = %s AND path = ANY(%s)",
            (snapshot_id, base_id, paths),
        )
        return cur.rowcount


async def save_repo_map(pool: AsyncConnectionPool, snapshot_id: int, file_tree: str, readme_summary: str,
                        paths: list[str]) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO repo_maps (snapshot_id, file_tree, readme_summary, paths) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (snapshot_id) DO UPDATE SET file_tree = EXCLUDED.file_tree, "
            "readme_summary = EXCLUDED.readme_summary, paths = EXCLUDED.paths",
            (snapshot_id, file_tree, readme_summary, paths),
        )
