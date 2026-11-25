"""Live indexing progress over Server-Sent Events (polls the snapshot row; ends when indexing finishes)."""

import asyncio
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from api.core import snapshots
from api.routers.repos import snapshot_out

router = APIRouter(prefix="/api")

TERMINAL = {"ready", "failed", "rejected"}
POLL_SECONDS = 1.0
MAX_STREAM_SECONDS = 30 * 60


def sse(event: str, data: dict | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


@router.get("/snapshots/{snapshot_id}/events")
async def progress_events(snapshot_id: int, request: Request) -> StreamingResponse:
    pool = request.app.state.pool

    async def stream():
        last = None
        started = time.monotonic()
        while time.monotonic() - started < MAX_STREAM_SECONDS:
            if await request.is_disconnected():
                return
            row = await snapshots.get_snapshot(pool, snapshot_id)
            if row is None:
                yield sse("error", {"code": "not_found", "message": "Unknown snapshot."})
                return
            current = snapshot_out(row).model_dump()
            if current != last:
                yield sse("progress", current)
                last = current
            else:
                yield ": keep-alive\n\n"
            if row["status"] in TERMINAL:
                return
            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
