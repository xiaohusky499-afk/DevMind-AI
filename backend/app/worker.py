"""Standalone document worker.

Run from the repository root:
    uv run python -m backend.app.worker
"""

import asyncio
import logging
import os
import socket

from .core.config import settings
from .core.db import db
from .core.observability import configure_logging
from .services.document_task_service import run_once

logger = logging.getLogger(__name__)


async def run() -> None:
    worker_id = os.getenv("DOCUMENT_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}"
    await db.connect()
    logger.info(
        "Document worker started",
        extra={"event": "document.worker.started", "worker_id": worker_id},
    )
    try:
        while True:
            if not await run_once(worker_id):
                await asyncio.sleep(settings.DOCUMENT_WORKER_POLL_SECONDS)
    finally:
        await db.close()


def main() -> None:
    configure_logging(settings.LOG_LEVEL)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Document worker stopped", extra={"event": "document.worker.stopped"})


if __name__ == "__main__":
    main()
