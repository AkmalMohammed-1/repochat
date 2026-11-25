"""Minimal async GitHub REST client: repo metadata, ref -> SHA, tarball download, compare.

Handles rate limits with backoff. The token (if any) stays server-side.
"""

import asyncio
import random
import time
from dataclasses import dataclass

import httpx

from api.core.config import get_settings


class GitHubError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class RepoInfo:
    owner: str
    name: str
    default_branch: str
    private: bool
    size_kb: int


class GitHubClient:
    def __init__(self, token: str | None = None, base_url: str | None = None):
        s = get_settings()
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "repochat"}
        token = token if token is not None else s.github_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(base_url=base_url or s.github_api_url, headers=headers,
                                         timeout=httpx.Timeout(30.0, read=120.0), follow_redirects=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, url: str, **kw) -> httpx.Response:
        """Request with retries: exponential backoff on 5xx/network errors, wait on short rate-limit resets."""
        for attempt in range(4):
            try:
                resp = await self._client.request(method, url, **kw)
            except httpx.TransportError:
                if attempt == 3:
                    raise GitHubError("github_unavailable", "Could not reach GitHub.", 502) from None
                await asyncio.sleep(2**attempt + random.random())
                continue
            if resp.status_code in (403, 429) and (
                resp.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in resp.headers
            ):
                wait = float(resp.headers.get("retry-after") or 0)
                if not wait and resp.headers.get("x-ratelimit-reset"):
                    wait = float(resp.headers["x-ratelimit-reset"]) - time.time()
                if 0 < wait <= 60 and attempt < 3:
                    await asyncio.sleep(wait + 1)
                    continue
                raise GitHubError("github_rate_limited",
                                  "GitHub API rate limit reached. Please try again in a few minutes.", 503)
            if resp.status_code >= 500 and attempt < 3:
                await asyncio.sleep(2**attempt + random.random())
                continue
            return resp
        raise AssertionError("unreachable")

    async def get_repo(self, owner: str, name: str) -> RepoInfo:
        resp = await self._request("GET", f"/repos/{owner}/{name}")
        if resp.status_code == 404:
            # GitHub returns 404 for private repos too, so the message covers both.
            raise GitHubError("not_found", f"{owner}/{name} doesn't exist or is private.", 404)
        resp.raise_for_status()
        data = resp.json()
        if data.get("private"):
            raise GitHubError("private", "Only public repositories are supported.", 403)
        return RepoInfo(owner=data["owner"]["login"], name=data["name"],
                        default_branch=data["default_branch"], private=False, size_kb=data.get("size", 0))

    async def resolve_sha(self, owner: str, name: str, ref: str) -> str:
        resp = await self._request("GET", f"/repos/{owner}/{name}/commits/{ref}",
                                   headers={"Accept": "application/vnd.github.sha"})
        if resp.status_code in (404, 422):
            raise GitHubError("ref_not_found", f"Branch or ref '{ref}' was not found in {owner}/{name}.", 404)
        resp.raise_for_status()
        return resp.text.strip()

    async def download_tarball(self, owner: str, name: str, sha: str, max_bytes: int) -> bytes:
        async with self._client.stream("GET", f"/repos/{owner}/{name}/tarball/{sha}") as resp:
            if resp.status_code == 404:
                raise GitHubError("not_found", f"Could not download {owner}/{name}@{sha}.", 404)
            resp.raise_for_status()
            buf = bytearray()
            async for part in resp.aiter_bytes():
                buf.extend(part)
                if len(buf) > max_bytes:
                    raise GitHubError("too_large",
                                      f"Repository archive is larger than {max_bytes // (1024 * 1024)} MB.", 413)
            return bytes(buf)

    async def compare(self, owner: str, name: str, base: str, head: str) -> list[dict] | None:
        """Changed files between two commits, or None if GitHub can't give a complete list."""
        resp = await self._request("GET", f"/repos/{owner}/{name}/compare/{base}...{head}")
        if resp.status_code != 200:
            return None
        files = resp.json().get("files", [])
        if len(files) >= 300:  # GitHub truncates the list at 300 files
            return None
        return files
