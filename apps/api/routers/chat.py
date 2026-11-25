"""POST /api/chat: run the LangGraph pipeline and stream events as SSE.

Events: sources {chunks} -> token {text} ... -> done {citations} | error {code, message}
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.core import snapshots
from api.core.config import get_settings
from api.core.llm import LLMNotConfigured, get_llm
from api.core.ratelimit import enforce
from api.core.repo_ref import github_line_url
from api.graph.citations import resolve_citations
from api.graph.graph import Deps, build_graph
from api.routers.status import sse

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    role: str
    content: str = Field(max_length=20_000)


class ChatRequest(BaseModel):
    snapshot_id: int
    question: str = Field(min_length=1, max_length=4_000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)
    focus_path: str | None = None


def _source(c: dict, snap: dict) -> dict:
    return {
        "id": c["id"], "path": c["path"], "symbol_name": c["symbol_name"], "kind": c["kind"],
        "language": c["language"], "start_line": c["start_line"], "end_line": c["end_line"],
        "content": c["content"],
        "url": github_line_url(snap["owner"], snap["repo"], snap["sha"], c["path"], c["start_line"], c["end_line"]),
    }


@router.post("/chat")
async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
    enforce(request, "chat", get_settings().rate_limit_chat_per_minute)
    pool = request.app.state.pool
    snap = await snapshots.get_snapshot(pool, body.snapshot_id)
    if snap is None:
        raise HTTPException(404, {"code": "not_found", "message": "Unknown repository snapshot."})
    if snap["status"] != "ready":
        raise HTTPException(409, {"code": "not_ready", "message": "This repository is still being indexed."})
    try:
        llm = get_llm()
    except LLMNotConfigured as e:
        raise HTTPException(503, {"code": "llm_not_configured", "message": str(e)}) from e

    graph = build_graph(Deps(pool=pool, embedder=request.app.state.embedder(), llm=llm))
    overview = await snapshots.get_repo_map(pool, snap["id"])
    state = {
        "question": body.question,
        "history": [m.model_dump() for m in body.history],
        "snapshot": {"id": snap["id"], "owner": snap["owner"], "repo": snap["repo"], "sha": snap["sha"]},
        "focus_path": body.focus_path,
        "overview": overview,
    }

    async def stream():
        chunks: list[dict] = []
        answer: list[str] = []
        try:
            async for event in graph.astream(state, stream_mode="custom"):
                if event["type"] == "sources":
                    chunks = event["chunks"]
                    yield sse("sources", {"chunks": [_source(c, snap) for c in chunks]})
                elif event["type"] == "token":
                    answer.append(event["text"])
                    yield sse("token", {"text": event["text"]})
        except Exception:
            log.exception("chat failed")
            yield sse("error", {"code": "chat_failed", "message": "Something went wrong generating the answer."})
            return
        citations = resolve_citations("".join(answer), chunks, snap["owner"], snap["repo"], snap["sha"])
        yield sse("done", {"citations": [c.to_dict() for c in citations]})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
