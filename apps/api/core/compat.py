"""psycopg's async mode can't use Windows' default ProactorEventLoop, so scripts start through run()."""

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coro)
