"""运行中 PostgreSQL 的可观测指标验收。"""

import asyncio

from backend.app.core.db import db
from backend.app.core.observability import render_operational_metrics


async def main() -> None:
    await db.connect()
    try:
        metrics = await render_operational_metrics()
    finally:
        await db.close()
    required = (
        "devmind_document_tasks",
        "devmind_document_stale_tasks",
        "devmind_rag_retrieval_runs_5m",
        "devmind_rag_retrieval_latency_ms",
        "devmind_rag_failed_answers_15m",
    )
    missing = [name for name in required if name not in metrics]
    if missing:
        raise AssertionError(f"missing metrics: {missing}")
    print("observability: PostgreSQL document and RAG metrics passed")


if __name__ == "__main__":
    asyncio.run(main())
