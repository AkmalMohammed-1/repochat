"""SQS (job queue) and S3 (repo tarball snapshots). boto3 is sync, so callers wrap calls in asyncio.to_thread.

In local dev AWS_ENDPOINT_URL points at LocalStack; in prod it is unset and the real AWS endpoints are used.
"""

import json
from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from api.core.config import get_settings


@lru_cache
def _client(service: str):
    s = get_settings()
    return boto3.client(service, region_name=s.aws_region, endpoint_url=s.aws_endpoint_url)


def sqs():
    return _client("sqs")


def s3():
    return _client("s3")


@lru_cache
def queue_url() -> str:
    return sqs().get_queue_url(QueueName=get_settings().sqs_queue_name)["QueueUrl"]


def enqueue_index_job(snapshot_id: int) -> None:
    """The message only carries the snapshot id; the worker reads everything else from Postgres."""
    sqs().send_message(QueueUrl=queue_url(), MessageBody=json.dumps({"snapshot_id": snapshot_id}))


def snapshot_key(owner: str, repo: str, sha: str) -> str:
    return f"repos/{owner}/{repo}/{sha}.tar.gz"


def s3_exists(key: str) -> bool:
    try:
        s3().head_object(Bucket=get_settings().s3_bucket, Key=key)
        return True
    except ClientError:
        return False


def s3_put(key: str, data: bytes) -> None:
    s3().put_object(Bucket=get_settings().s3_bucket, Key=key, Body=data)


def s3_get(key: str) -> bytes:
    return s3().get_object(Bucket=get_settings().s3_bucket, Key=key)["Body"].read()


def ensure_dev_resources() -> None:
    """Create the bucket, the queue and its dead-letter queue if missing. Used against LocalStack;
    in AWS these are created once by hand / IaC (see infra/aws/README.md)."""
    s = get_settings()
    try:
        s3().head_bucket(Bucket=s.s3_bucket)
    except ClientError:
        s3().create_bucket(Bucket=s.s3_bucket)
    dlq_url = sqs().create_queue(QueueName=s.sqs_dlq_name)["QueueUrl"]
    dlq_arn = sqs().get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    sqs().create_queue(
        QueueName=s.sqs_queue_name,
        Attributes={
            "VisibilityTimeout": str(s.job_timeout_seconds + 60),
            "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn,
                                         "maxReceiveCount": str(s.max_job_attempts)}),
        },
    )
