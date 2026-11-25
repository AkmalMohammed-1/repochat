"""SQS consumer: long-polls the index queue and runs one indexing job per message.

Retry policy:
  - success / rejected (too big etc.)  -> delete message
  - failure, attempts left             -> leave message; make it visible again after an exponential backoff
  - failure on the last attempt        -> mark snapshot failed; SQS moves the message to the DLQ
Each job has a hard timeout (JOB_TIMEOUT_SECONDS). WORKER_CONCURRENCY caps parallel jobs per process.

Run: python -m worker.main
"""

import asyncio
import json
import logging
import signal

from api.core import aws
from api.core.compat import run
from api.core.config import get_settings
from api.core.db import create_pool
from api.core.embeddings import get_embedder
from api.core.github import GitHubClient, GitHubError
from worker.pipeline import store
from worker.pipeline.run import RejectedError, index_snapshot

log = logging.getLogger("worker")


async def handle(message: dict, pool, gh, embedder) -> None:
    s = get_settings()
    receipt = message["ReceiptHandle"]
    attempt = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
    snapshot_id = int(json.loads(message["Body"])["snapshot_id"])
    log.info("job snapshot=%s attempt=%s", snapshot_id, attempt)
    try:
        await store.update_snapshot(pool, snapshot_id, attempts=attempt)
        await asyncio.wait_for(index_snapshot(pool, snapshot_id, embedder=embedder, gh=gh),
                               timeout=s.job_timeout_seconds)
    except (RejectedError, GitHubError) as e:
        retryable = isinstance(e, GitHubError) and e.code in ("github_rate_limited", "github_unavailable")
        if not retryable:
            log.info("snapshot %s rejected: %s", snapshot_id, e)
            await store.update_snapshot(pool, snapshot_id, status="rejected", error=str(e))
            await asyncio.to_thread(aws.sqs().delete_message, QueueUrl=aws.queue_url(), ReceiptHandle=receipt)
            return
        await _fail(pool, snapshot_id, attempt, receipt, str(e))
        return
    except Exception as e:  # noqa: BLE001 - any crash is retried
        log.exception("snapshot %s failed", snapshot_id)
        msg = "Indexing timed out." if isinstance(e, TimeoutError) else f"Indexing failed: {e}"
        await _fail(pool, snapshot_id, attempt, receipt, msg)
        return
    await asyncio.to_thread(aws.sqs().delete_message, QueueUrl=aws.queue_url(), ReceiptHandle=receipt)


async def _fail(pool, snapshot_id: int, attempt: int, receipt: str, error: str) -> None:
    s = get_settings()
    if attempt >= s.max_job_attempts:
        # Leave the message: on the next receive SQS redrives it to the DLQ for inspection.
        await store.update_snapshot(pool, snapshot_id, status="failed", error=error)
        return
    await store.update_snapshot(pool, snapshot_id, status="queued",
                                error=f"{error} (retrying, attempt {attempt + 1} of {s.max_job_attempts})")
    backoff = min(30 * 2 ** (attempt - 1), 900)
    await asyncio.to_thread(aws.sqs().change_message_visibility, QueueUrl=aws.queue_url(),
                            ReceiptHandle=receipt, VisibilityTimeout=backoff)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    pool = await create_pool(max_size=max(4, s.worker_concurrency * 2))
    gh = GitHubClient()
    embedder = get_embedder()  # load the model once, before taking jobs
    stop = asyncio.Event()
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
    except (NotImplementedError, RuntimeError):
        pass  # Windows: Ctrl+C raises KeyboardInterrupt instead
    log.info("worker ready (concurrency=%s)", s.worker_concurrency)
    while not stop.is_set():
        resp = await asyncio.to_thread(
            aws.sqs().receive_message, QueueUrl=aws.queue_url(), MaxNumberOfMessages=s.worker_concurrency,
            WaitTimeSeconds=20, AttributeNames=["ApproximateReceiveCount"],
        )
        messages = resp.get("Messages", [])
        if messages:
            await asyncio.gather(*(handle(m, pool, gh, embedder) for m in messages))
    await gh.aclose()
    await pool.close()


if __name__ == "__main__":
    run(main())
