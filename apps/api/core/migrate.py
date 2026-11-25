"""Tiny SQL migration runner: applies migrations/*.sql in filename order, once each.

Run: python -m api.core.migrate
"""

from pathlib import Path

import psycopg

from api.core.compat import run
from api.core.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


async def migrate(database_url: str | None = None) -> list[str]:
    applied: list[str] = []
    url = database_url or get_settings().database_url
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        # Advisory lock so two containers starting together don't both migrate.
        await conn.execute("SELECT pg_advisory_lock(727272)")
        try:
            cur = await conn.execute("SELECT name FROM schema_migrations")
            done = {row[0] for row in await cur.fetchall()}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.name in done:
                    continue
                async with conn.transaction():
                    await conn.execute(path.read_text(encoding="utf-8"))
                    await conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
                applied.append(path.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock(727272)")
    return applied


if __name__ == "__main__":
    for name in run(migrate()):
        print(f"applied {name}")
