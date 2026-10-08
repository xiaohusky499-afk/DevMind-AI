"""Live check for durable document-task claim, retry and dead-letter states."""

import asyncio
from uuid import uuid4

from backend.app.core.db import db
from backend.app.services.document_task_service import (
    claim_next_task,
    mark_failure,
    recover_stale_tasks,
)


async def main() -> None:
    document_id, task_id = uuid4(), uuid4()
    await db.connect()
    try:
        await db.execute(
            """INSERT INTO documents
               (id, knowledge_base_id, name, doc_source, source_type, doc_type,
                status, checksum)
               VALUES ($1, '00000000-0000-0000-0000-000000000101', $2, $3,
                       'md', 'md', 'uploaded', $4)""",
            (document_id, "worker-live-check.md", f"worker-{document_id}.md", str(document_id)),
        )
        await db.execute(
            """INSERT INTO document_tasks
               (id, document_id, task_type, max_attempts, timeout_seconds)
               VALUES ($1,$2,'ingest',2,60)""",
            (task_id, document_id),
        )

        first = await claim_next_task("live-check-worker")
        assert first and first["id"] == task_id and first["attempt_count"] == 1
        assert await mark_failure(first, "LIVE_CHECK", "first failure") == "retry_wait"
        await db.execute(
            "UPDATE document_tasks SET available_at=now() WHERE id=$1", (task_id,)
        )

        second = await claim_next_task("live-check-worker")
        assert second and second["id"] == task_id and second["attempt_count"] == 2
        assert await mark_failure(second, "LIVE_CHECK", "second failure") == "dead"
        final = await db.fetch_one(
            "SELECT status, attempt_count FROM document_tasks WHERE id=$1", (task_id,)
        )
        assert final == {"status": "dead", "attempt_count": 2}

        await db.execute(
            """UPDATE document_tasks SET status='running', attempt_count=1,
                      worker_id='crashed-worker', lease_expires_at=now()-interval '1 second'
               WHERE id=$1""",
            (task_id,),
        )
        assert await recover_stale_tasks() >= 1
        recovered = await db.fetch_one(
            "SELECT status, worker_id FROM document_tasks WHERE id=$1", (task_id,)
        )
        assert recovered == {"status": "retry_wait", "worker_id": None}
        print("document worker live check passed")
    finally:
        await db.execute("DELETE FROM documents WHERE id=$1", (document_id,))
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
