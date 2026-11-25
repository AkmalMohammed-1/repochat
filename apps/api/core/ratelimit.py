"""In-memory sliding-window rate limiter, per client IP and bucket.

Good enough for one API instance. With several instances behind a load balancer each keeps its own window,
so the effective limit is N x the configured one (a shared store like Redis would fix that).
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from api.core.config import get_settings


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def check(self, bucket: str, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        hits = self._hits[(bucket, key)]
        while hits and hits[0] <= now - window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce(request: Request, bucket: str, limit: int) -> None:
    if not limiter.check(bucket, client_ip(request), limit):
        raise HTTPException(status_code=429, detail={
            "code": "rate_limited", "message": "Too many requests. Please wait a minute and try again."})
