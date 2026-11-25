"""Get the repo's files for an exact commit: S3 cache first, else GitHub tarball (then cached to S3)."""

import asyncio
import io
import tarfile
from pathlib import Path

from api.core import aws
from api.core.github import GitHubClient


def extract_tarball(data: bytes, dest: Path, max_file_bytes: int) -> int:
    """Extract regular files only, stripping GitHub's '<owner>-<repo>-<sha>/' top folder.
    filter='data' rejects absolute paths, '..' traversal and links pointing outside dest."""
    count = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar:
            if not member.isfile() or member.size > max_file_bytes:
                continue
            parts = member.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            member.name = parts[1]
            try:
                tar.extract(member, dest, filter="data")
                count += 1
            except (tarfile.FilterError, OSError):
                continue  # unsafe or un-writable path (e.g. invalid on this OS): skip the file
    return count


async def fetch_snapshot(gh: GitHubClient, owner: str, repo: str, sha: str, dest: Path,
                         max_tarball_bytes: int, max_file_bytes: int) -> None:
    key = aws.snapshot_key(owner, repo, sha)
    if await asyncio.to_thread(aws.s3_exists, key):
        data = await asyncio.to_thread(aws.s3_get, key)
    else:
        data = await gh.download_tarball(owner, repo, sha, max_tarball_bytes)
        await asyncio.to_thread(aws.s3_put, key, data)
    await asyncio.to_thread(extract_tarball, data, dest, max_file_bytes)
