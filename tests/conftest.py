import asyncio
import os
import sys

import pytest

# Tests never download models or call LLMs.
os.environ.setdefault("EMBEDDING_PROVIDER", "hash")
os.environ.setdefault("LLM_PROVIDER", "none")


def pytest_asyncio_loop_factories(config, item):
    # psycopg's async mode doesn't support Windows' default Proactor loop.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(scope="session")
async def pool():
    """A pool on a fresh, migrated database. Skips integration tests if Postgres isn't running."""
    import psycopg

    from api.core.config import get_settings
    from api.core.db import create_pool
    from api.core.migrate import migrate

    url = os.environ.get("TEST_DATABASE_URL") or get_settings().database_url
    try:
        conn = await psycopg.AsyncConnection.connect(url, connect_timeout=3)
        await conn.close()
    except Exception:
        pytest.skip("Postgres with pgvector not reachable (set TEST_DATABASE_URL or run docker compose up db)")
    await migrate(url)
    p = await create_pool(url, max_size=4)
    yield p
    await p.close()
