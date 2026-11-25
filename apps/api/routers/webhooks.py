"""GitHub push webhook: re-index a repo when its default branch moves.

Only repos already known to RepoChat are re-indexed. The worker re-embeds only files that changed between
the last ready snapshot and the new commit, and copies the rest forward.
"""

import hashlib
import hmac
import json

from fastapi import APIRouter, Header, HTTPException, Request

from api.core import snapshots
from api.core.config import get_settings

router = APIRouter()


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhooks/github")
async def github_webhook(request: Request, x_github_event: str = Header(""),
                         x_hub_signature_256: str | None = Header(None)) -> dict:
    body = await request.body()
    if not verify_signature(get_settings().github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(401, {"code": "bad_signature", "message": "Invalid webhook signature."})
    if x_github_event == "ping":
        return {"ok": True, "pong": True}
    if x_github_event != "push":
        return {"ok": True, "ignored": f"event {x_github_event}"}

    payload = json.loads(body)
    repo = payload.get("repository") or {}
    default_branch = repo.get("default_branch")
    after = payload.get("after", "")
    if payload.get("ref") != f"refs/heads/{default_branch}" or payload.get("deleted") or set(after) == {"0"}:
        return {"ok": True, "ignored": "not a push to the default branch"}
    owner = (repo.get("owner") or {}).get("login") or (repo.get("owner") or {}).get("name")
    pool = request.app.state.pool
    known = await snapshots.find_repo(pool, owner, repo.get("name", ""))
    if known is None:
        return {"ok": True, "ignored": "repository not indexed by RepoChat"}
    snapshot_id = await snapshots.ensure_indexed(pool, known["id"], after, get_settings().index_strategy)
    return {"ok": True, "snapshot_id": snapshot_id}
