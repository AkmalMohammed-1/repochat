"""Parse RepoChat paths / GitHub URLs into (owner, repo, branch, path)."""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")  # GitHub username/org rules
_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


@dataclass(frozen=True)
class RepoRef:
    owner: str
    repo: str
    branch: str | None = None
    path: str | None = None


def parse_repo_path(path_or_url: str) -> RepoRef | None:
    """Accepts 'owner/repo', '/owner/repo/tree/branch', '/owner/repo/blob/branch/a/b.py',
    or a full https://github.com/... URL. Returns None if it isn't a valid repo reference.

    Branch names containing '/' are ambiguous in blob URLs; we take the first segment as the branch.
    """
    text = path_or_url.strip()
    if "://" in text or text.startswith("github.com"):
        parsed = urlparse(text if "://" in text else f"https://{text}")
        text = parsed.path
    parts = [p for p in text.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not _NAME.match(owner) or not _REPO.match(repo) or repo in (".", ".."):
        return None
    rest = parts[2:]
    if not rest:
        return RepoRef(owner, repo)
    if rest[0] == "tree" and len(rest) >= 2:
        return RepoRef(owner, repo, branch="/".join(rest[1:]))
    if rest[0] == "blob" and len(rest) >= 3:
        return RepoRef(owner, repo, branch=rest[1], path="/".join(rest[2:]))
    return None


def github_line_url(owner: str, repo: str, sha: str, path: str, start: int, end: int) -> str:
    anchor = f"#L{start}" if start == end else f"#L{start}-L{end}"
    return f"https://github.com/{owner}/{repo}/blob/{sha}/{path}{anchor}"
