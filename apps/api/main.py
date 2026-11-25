"""FastAPI app. Run: uvicorn api.main:app --app-dir apps --port 8000"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.core.config import get_settings
from api.core.db import create_pool
from api.core.embeddings import get_embedder
from api.core.github import GitHubClient
from api.routers import chat, repos, status, webhooks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await create_pool()
    app.state.gh = GitHubClient()
    app.state.embedder = get_embedder  # cached factory; first call loads the model
    # Load the embedding model in the background so the first question isn't slow.
    warmup = asyncio.create_task(asyncio.to_thread(get_embedder))
    yield
    warmup.cancel()
    await app.state.gh.aclose()
    await app.state.pool.close()


app = FastAPI(title="RepoChat API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(repos.router)
app.include_router(status.router)
app.include_router(chat.router)
app.include_router(webhooks.router)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
