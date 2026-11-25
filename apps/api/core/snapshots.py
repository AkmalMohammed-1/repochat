"""Repo/snapshot bookkeeping shared by the repos router and the GitHub webhook."""

import asyncio
import logging

from psycopg_pool import AsyncConnectionPool

from api.core import aws

log = logging.getLogger(__name__)

SNAPSHOT_FIELDS = ("s.id, s.sha, s.strategy, s.status, s.error, s.files_total, s.files_done, s.chunks_done, "
                   "s.created_at, s.updated_at, r.owner, r.name AS repo")


async def upsert_repo(pool: AsyncConnectionPool, owner: str, name: str, default_branch: str | None) -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO repos (owner, name, default_branch) VALUES (%s, %s, %s) "
            "ON CONFLICT ((lower(owner)), (lower(name))) DO UPDATE SET owner = EXCLUDED.owner, "
            "name = EXCLUDED.name, default_branch = COALESCE(EXCLUDED.default_branch, repos.default_branch) "
            "RETURNING id",
            (owner, name, default_branch),
        )
        return (await cur.fetchone())["id"]


async def find_repo(pool: AsyncConnectionPool, owner: str, name: str) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT * FROM repos WHERE lower(owner) = lower(%s) AND lower(name) = lower(%s)",
                                 (owner, name))
        return await cur.fetchone()


async def get_snapshot(pool: AsyncConnectionPool, snapshot_id: int) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            f"SELECT {SNAPSHOT_FIELDS} FROM snapshots s JOIN repos r ON r.id = s.repo_id WHERE s.id = %s",
            (snapshot_id,))
        return await cur.fetchone()


async def latest_ready(pool: AsyncConnectionPool, repo_id: int, strategy: str) -> int | None:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id FROM snapshots WHERE repo_id = %s AND strategy = %s AND status = 'ready' "
            "ORDER BY finished_at DESC LIMIT 1", (repo_id, strategy))
        row = await cur.fetchone()
        return row["id"] if row else None


async def ensure_indexed(pool: AsyncConnectionPool, repo_id: int, sha: str, strategy: str,
                         enqueue: bool = True) -> int:
    """Return the snapshot id for (repo, sha, strategy), creating + enqueueing a job when needed.

    Safe under concurrent requests: the unique constraint means only one request creates the row,
    and only the creator enqueues. Failed snapshots are re-queued on the next visit.
    """
    base_id = await latest_ready(pool, repo_id, strategy)
    async with pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO snapshots (repo_id, sha, strategy, base_snapshot_id) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (repo_id, sha, strategy) DO NOTHING RETURNING id",
            (repo_id, sha, strategy, base_id))
        created = await cur.fetchone()
        if created:
            snapshot_id, should_enqueue = created["id"], True
        else:
            cur = await conn.execute(
                "UPDATE snapshots SET status = 'queued', error = NULL, updated_at = now() "
                "WHERE repo_id = %s AND sha = %s AND strategy = %s AND status = 'failed' RETURNING id",
                (repo_id, sha, strategy))
            requeued = await cur.fetchone()
            if requeued:
                snapshot_id, should_enqueue = requeued["id"], True
            else:
                cur = await conn.execute(
                    "SELECT id FROM snapshots WHERE repo_id = %s AND sha = %s AND strategy = %s",
                    (repo_id, sha, strategy))
                snapshot_id, should_enqueue = (await cur.fetchone())["id"], False
    if should_enqueue and enqueue:
        try:
            await asyncio.to_thread(aws.enqueue_index_job, snapshot_id)
        except Exception:
            log.exception("could not enqueue snapshot %s", snapshot_id)
            async with pool.connection() as conn:
                await conn.execute("UPDATE snapshots SET status = 'failed', error = %s WHERE id = %s",
                                   ("Could not queue the indexing job. Please retry.", snapshot_id))
    return snapshot_id


async def get_repo_map(pool: AsyncConnectionPool, snapshot_id: int) -> dict | None:
    async with pool.connection() as conn:
        cur = await conn.execute("SELECT file_tree, readme_summary, paths FROM repo_maps WHERE snapshot_id = %s",
                                 (snapshot_id,))
        return await cur.fetchone()
