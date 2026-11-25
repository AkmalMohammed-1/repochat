"""One-shot startup task: run DB migrations, and create queue/bucket when pointed at LocalStack.

Run: python -m api.core.bootstrap
"""

import asyncio

from api.core.aws import ensure_dev_resources
from api.core.compat import run
from api.core.config import get_settings
from api.core.migrate import migrate


async def main() -> None:
    for name in await migrate():
        print(f"applied migration {name}")
    if get_settings().aws_endpoint_url:
        await asyncio.to_thread(ensure_dev_resources)
        print("LocalStack queue + bucket ready")


if __name__ == "__main__":
    run(main())
