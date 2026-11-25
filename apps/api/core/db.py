"""Async Postgres connection pool (psycopg 3) with the pgvector type registered on every connection."""

from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from api.core.config import get_settings


async def _configure(conn: AsyncConnection) -> None:
    await register_vector_async(conn)


async def create_pool(database_url: str | None = None, max_size: int = 10) -> AsyncConnectionPool:
    pool = AsyncConnectionPool(
        conninfo=database_url or get_settings().database_url,
        min_size=1,
        max_size=max_size,
        kwargs={"row_factory": dict_row, "autocommit": True},
        configure=_configure,
        open=False,
    )
    await pool.open(wait=True, timeout=30)
    return pool
