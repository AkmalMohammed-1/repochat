"""Resolve a repo URL to a commit and an index snapshot; report status; suggest starter questions."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.core import snapshots
from api.core.config import get_settings
from api.core.github import GitHubError
from api.core.ratelimit import enforce
from api.core.repo_ref import parse_repo_path

router = APIRouter(prefix="/api")

MAX_REPO_SIZE_KB = 1_000_000  # 1 GB of git history: reject before downloading anything


class ResolveRequest(BaseModel):
    url: str | None = Field(None, description="GitHub URL or RepoChat path, e.g. /owner/repo/tree/main")
    owner: str | None = None
    repo: str | None = None
    branch: str | None = None


class SnapshotOut(BaseModel):
    snapshot_id: int
    owner: str
    repo: str
    sha: str
    branch: str | None = None
    focus_path: str | None = None
    status: str
    error: str | None = None
    files_total: int
    files_done: int
    chunks_done: int


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def snapshot_out(row: dict, branch: str | None = None, focus_path: str | None = None) -> SnapshotOut:
    return SnapshotOut(snapshot_id=row["id"], owner=row["owner"], repo=row["repo"], sha=row["sha"],
                       branch=branch, focus_path=focus_path, status=row["status"], error=row["error"],
                       files_total=row["files_total"], files_done=row["files_done"],
                       chunks_done=row["chunks_done"])


@router.post("/repos/resolve", response_model=SnapshotOut)
async def resolve(body: ResolveRequest, request: Request) -> SnapshotOut:
    enforce(request, "index", get_settings().rate_limit_index_per_minute)
    if body.url:
        ref = parse_repo_path(body.url)
    elif body.owner and body.repo:
        ref = parse_repo_path(f"{body.owner}/{body.repo}" + (f"/tree/{body.branch}" if body.branch else ""))
    else:
        ref = None
    if ref is None:
        raise _err(400, "invalid_url", "That doesn't look like a GitHub repository URL (github.com/owner/repo).")

    pool, gh = request.app.state.pool, request.app.state.gh
    try:
        info = await gh.get_repo(ref.owner, ref.repo)
        if info.size_kb > MAX_REPO_SIZE_KB:
            raise _err(413, "too_large", "This repository is too large for RepoChat to index.")
        branch = ref.branch or info.default_branch
        sha = await gh.resolve_sha(info.owner, info.name, branch)
    except GitHubError as e:
        raise _err(e.status, e.code, e.message) from e

    repo_id = await snapshots.upsert_repo(pool, info.owner, info.name, info.default_branch)
    snapshot_id = await snapshots.ensure_indexed(pool, repo_id, sha, get_settings().index_strategy)
    row = await snapshots.get_snapshot(pool, snapshot_id)
    return snapshot_out(row, branch=branch, focus_path=ref.path)


@router.get("/snapshots/{snapshot_id}", response_model=SnapshotOut)
async def get_status(snapshot_id: int, request: Request) -> SnapshotOut:
    row = await snapshots.get_snapshot(request.app.state.pool, snapshot_id)
    if row is None:
        raise _err(404, "not_found", "Unknown snapshot.")
    return snapshot_out(row)


def suggest_questions(paths: list[str]) -> list[str]:
    """Cheap, deterministic starter questions based on what files the repo contains."""
    lower = [p.lower() for p in paths]
    out = ["What does this project do?", "Where is the entry point?"]
    if any(p.endswith((".sql", "models.py", "schema.prisma")) or "migration" in p or "/models/" in p
           for p in lower):
        out.append("How is data stored?")
    if any(p.startswith(("tests/", "test/")) or "/tests/" in p or ".test." in p or "_test." in p for p in lower):
        out.append("How are the tests organised and run?")
    if any(p.endswith("dockerfile") or p.startswith(".github/workflows") or "docker-compose" in p for p in lower):
        out.append("How is this built and deployed?")
    if any("auth" in p for p in lower):
        out.append("How does authentication work?")
    if any("/api/" in p or "routes" in p or "router" in p or "controller" in p for p in lower):
        out.append("What API endpoints are there?")
    if len(out) < 5:
        out.append("What are the main modules and how do they fit together?")
    return out[:6]


@router.get("/snapshots/{snapshot_id}/suggestions")
async def suggestions(snapshot_id: int, request: Request) -> dict:
    repo_map = await snapshots.get_repo_map(request.app.state.pool, snapshot_id)
    return {"questions": suggest_questions(repo_map["paths"] if repo_map else [])}
