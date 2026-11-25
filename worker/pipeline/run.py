"""The indexing job: fetch -> filter -> (copy unchanged) -> chunk -> embed -> store -> repo map."""

import asyncio
import logging
import tempfile
from pathlib import Path

from psycopg_pool import AsyncConnectionPool

from api.core.config import Settings, get_settings
from api.core.embeddings import Embedder
from api.core.github import GitHubClient
from worker.pipeline import store
from worker.pipeline.chunk import STRATEGIES, chunk_file, embed_text, search_text
from worker.pipeline.fetch import fetch_snapshot
from worker.pipeline.filter import filter_files
from worker.pipeline.repomap import build_file_tree, summarize_readme

log = logging.getLogger(__name__)
FILES_PER_BATCH = 16


class RejectedError(Exception):
    """The repo can't be indexed (too big, etc.). Not retried."""


async def _changed_files(pool: AsyncConnectionPool, gh: GitHubClient, snap: dict) -> tuple[int, set[str]] | None:
    """(base_snapshot_id, paths changed since base) when an incremental re-index is possible."""
    base_id = snap.get("base_snapshot_id")
    if not base_id:
        return None
    base = await store.get_snapshot(pool, base_id)
    if not base or base["status"] != "ready" or base["strategy"] != snap["strategy"]:
        return None
    files = await gh.compare(snap["owner"], snap["repo"], base["sha"], snap["sha"])
    if files is None:
        return None
    changed = {f["filename"] for f in files}
    return base_id, changed


async def index_snapshot(pool: AsyncConnectionPool, snapshot_id: int, *, embedder: Embedder,
                         gh: GitHubClient | None = None, source_dir: Path | None = None,
                         settings: Settings | None = None, summarize: bool = True) -> None:
    s = settings or get_settings()
    snap = await store.get_snapshot(pool, snapshot_id)
    if snap is None or snap["status"] == "ready":
        return  # idempotent: already done
    strategy = STRATEGIES[snap["strategy"]]
    await store.update_snapshot(pool, snapshot_id, status="running", error=None, files_done=0, chunks_done=0)
    await store.clear_snapshot_data(pool, snapshot_id)

    with tempfile.TemporaryDirectory(prefix="repochat-") as tmp:
        root = source_dir
        if root is None:
            if gh is None:
                raise ValueError("need a GitHub client or a local source_dir")
            root = Path(tmp)
            await fetch_snapshot(gh, snap["owner"], snap["repo"], snap["sha"], root,
                                 s.max_tarball_bytes, s.max_file_bytes)

        result = await asyncio.to_thread(filter_files, root, s.max_file_bytes)
        if len(result.files) > s.max_files:
            raise RejectedError(f"Repository has {len(result.files)} indexable files; the limit is {s.max_files}.")
        if result.total_bytes > s.max_total_bytes:
            mb = result.total_bytes / 1024 / 1024
            raise RejectedError(f"Repository has {mb:.0f} MB of indexable text; the limit is "
                                f"{s.max_total_bytes // 1024 // 1024} MB.")
        if not result.files:
            raise RejectedError("No indexable text files were found in this repository.")
        await store.update_snapshot(pool, snapshot_id, files_total=len(result.files))

        todo = result.files
        files_done = chunks_done = 0
        incremental = await _changed_files(pool, gh, snap) if gh else None
        if incremental:
            base_id, changed = incremental
            unchanged = [p for p in result.files if p not in changed]
            chunks_done = await store.copy_chunks_forward(pool, base_id, snapshot_id, unchanged)
            files_done = len(unchanged)
            todo = [p for p in result.files if p in changed]
            log.info("snapshot %s: incremental, %d changed / %d copied", snapshot_id, len(todo), files_done)
            await store.update_snapshot(pool, snapshot_id, files_done=files_done, chunks_done=chunks_done)

        for i in range(0, len(todo), FILES_PER_BATCH):
            batch = todo[i:i + FILES_PER_BATCH]
            chunks = []
            for rel in batch:
                text = (root / rel).read_text(encoding="utf-8", errors="replace")
                chunks.extend(await asyncio.to_thread(chunk_file, rel, text, snap["strategy"]))
            if chunks:
                vectors = await embedder.embed_documents([embed_text(c, strategy.header) for c in chunks])
                await store.insert_chunks(pool, snapshot_id, chunks, vectors, [search_text(c) for c in chunks])
            files_done += len(batch)
            chunks_done += len(chunks)
            await store.update_snapshot(pool, snapshot_id, files_done=files_done, chunks_done=chunks_done)

        summary = await summarize_readme(root, result.files) if summarize else ""
        await store.save_repo_map(pool, snapshot_id, build_file_tree(result.files), summary, result.files)

    await store.update_snapshot(pool, snapshot_id, status="ready", error=None)
    log.info("snapshot %s ready: %d files, %d chunks", snapshot_id, files_done, chunks_done)
