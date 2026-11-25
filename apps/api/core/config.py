"""All runtime configuration, read from environment variables (and .env in local dev)."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database ---
    database_url: str = "postgresql://repochat:repochat@localhost:5432/repochat"

    # --- GitHub ---
    github_token: str = ""  # server-side only; raises the API rate limit from 60 to 5000 req/h
    github_api_url: str = "https://api.github.com"
    github_webhook_secret: str = ""

    # --- AWS (LocalStack in dev: set aws_endpoint_url=http://localhost:4566) ---
    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None
    sqs_queue_name: str = "repochat-index"
    sqs_dlq_name: str = "repochat-index-dlq"
    s3_bucket: str = "repochat-snapshots"

    # --- Models ---
    llm_provider: Literal["groq", "openai", "anthropic", "none"] = "groq"
    llm_model: str = ""  # empty -> provider default (see core/llm.py)
    groq_api_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    embedding_provider: Literal["local", "openai", "hash"] = "local"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384  # must match migrations/001_init.sql
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # --- Indexing limits ---
    max_files: int = 2000
    max_total_bytes: int = 50 * 1024 * 1024
    max_file_bytes: int = 1024 * 1024
    max_tarball_bytes: int = 300 * 1024 * 1024
    index_strategy: str = "ast_header"  # see worker/pipeline/chunk.py STRATEGIES

    # --- Worker ---
    worker_concurrency: int = 1
    job_timeout_seconds: int = 900
    max_job_attempts: int = 3  # must match the SQS redrive maxReceiveCount

    # --- Retrieval ---
    retrieve_k: int = 30  # candidates from each of vector + keyword search
    final_k: int = 8  # chunks kept after reranking

    # --- API ---
    cors_origins: str = "http://localhost:3000"
    rate_limit_index_per_minute: int = 10
    rate_limit_chat_per_minute: int = 30
    trust_proxy_headers: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
