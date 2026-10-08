"""PostgreSQL-backed durable execution for document tasks."""

import asyncio
import logging
import time
from uuid import UUID

from ..core.config import settings
from ..core.db import db

logger = logging.getLogger(__name__)


def retry_delay_seconds(attempt_count: int) -> int:
    """Bounded exponential retry delay."""
    return min(900, settings.DOCUMENT_TASK_RETRY_BASE_SECONDS * 2 ** max(0, attempt_count - 1))


async def recover_stale_tasks() -> int:
    """Release expired leases so another worker can resume the task."""
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            """SELECT id, document_id, task_type, attempt_count, max_attempts
               FROM document_tasks
               WHERE status='running'
                 AND (lease_expires_at IS NULL OR lease_expires_at < now())
               FOR UPDATE SKIP LOCKED"""
        )
        for row in rows:
            is_dead = row["attempt_count"] >= row["max_attempts"]
            await conn.execute(
                """UPDATE document_tasks
                   SET status=$2::varchar, available_at=now()+($3*interval '1 second'),
                       worker_id=NULL, heartbeat_at=NULL, lease_expires_at=NULL,
                       completed_at=CASE WHEN $2::varchar='dead' THEN now() ELSE NULL END,
                       error_code='WORKER_LEASE_EXPIRED',
                       error_message='Worker 心跳超时，任务已回收'
                   WHERE id=$1""",
                row["id"],
                "dead" if is_dead else "retry_wait",
                0 if is_dead else retry_delay_seconds(row["attempt_count"]),
            )
            if row["task_type"] != "delete":
                await conn.execute(
                    """UPDATE documents SET status=$2::varchar,
                              error_code='WORKER_LEASE_EXPIRED',
                              error_message=$3
                       WHERE id=$1""",
                    row["document_id"],
                    "failed" if is_dead else "uploaded",
                    "任务多次中断，已进入死信状态"
                    if is_dead
                    else "Worker 心跳超时，任务等待恢复",
                )
    if rows:
        logger.warning(
            "Expired document task leases recovered",
            extra={"event": "document.worker.leases_recovered", "result": len(rows)},
        )
    return len(rows)


async def claim_next_task(worker_id: str) -> dict | None:
    """Atomically claim one ready task across any number of workers."""
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """WITH candidate AS (
                   SELECT id FROM document_tasks
                   WHERE status IN ('pending','retry_wait') AND available_at <= now()
                   ORDER BY available_at, created_at
                   FOR UPDATE SKIP LOCKED
                   LIMIT 1
               )
               UPDATE document_tasks task
               SET status='running', progress=GREATEST(task.progress, 1),
                   attempt_count=task.attempt_count+1,
                   started_at=COALESCE(task.started_at, now()),
                   heartbeat_at=now(),
                   lease_expires_at=now()+($2*interval '1 second'),
                   worker_id=$1, completed_at=NULL
               FROM candidate
               WHERE task.id=candidate.id
               RETURNING task.id, task.document_id, task.task_type,
                         task.attempt_count, task.max_attempts, task.timeout_seconds""",
            worker_id,
            settings.DOCUMENT_TASK_LEASE_SECONDS,
        )
    return dict(row) if row else None


async def heartbeat(task_id: UUID, worker_id: str) -> bool:
    updated = await db.execute(
        """UPDATE document_tasks
           SET heartbeat_at=now(), lease_expires_at=now()+($3*interval '1 second')
           WHERE id=$1 AND worker_id=$2 AND status='running'""",
        (task_id, worker_id, settings.DOCUMENT_TASK_LEASE_SECONDS),
    )
    return updated == 1


async def mark_failure(task: dict, error_code: str, message: str) -> str:
    """Schedule a retry or move an exhausted task to dead-letter state."""
    is_dead = task["attempt_count"] >= task["max_attempts"]
    status = "dead" if is_dead else "retry_wait"
    delay = 0 if is_dead else retry_delay_seconds(task["attempt_count"])
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """UPDATE document_tasks
               SET status=$2::varchar, available_at=now()+($3*interval '1 second'),
                   worker_id=NULL, heartbeat_at=NULL, lease_expires_at=NULL,
                   error_code=$4, error_message=$5,
                   completed_at=CASE WHEN $2::varchar='dead' THEN now() ELSE NULL END
               WHERE id=$1 AND status='running'""",
            task["id"],
            status,
            delay,
            error_code,
            message[:1000],
        )
        if task["task_type"] != "delete":
            await conn.execute(
                """UPDATE documents
                   SET status=$2, error_code=$3, error_message=$4
                   WHERE id=$1""",
                task["document_id"],
                "failed" if is_dead else "uploaded",
                error_code,
                message[:1000],
            )
    return status


async def _heartbeat_loop(task_id: UUID, worker_id: str) -> None:
    while True:
        await asyncio.sleep(settings.DOCUMENT_WORKER_HEARTBEAT_SECONDS)
        if not await heartbeat(task_id, worker_id):
            return


async def execute_task(task: dict, worker_id: str) -> str:
    """Execute one claimed task and persist retry/dead-letter outcomes."""
    from . import document_service

    keep_alive = asyncio.create_task(_heartbeat_loop(task["id"], worker_id))
    started = time.perf_counter()
    try:
        if task["task_type"] in {"ingest", "reindex"}:
            operation = document_service.process_document(task["document_id"], task["id"])
        elif task["task_type"] == "delete":
            operation = document_service.cleanup_document(task["document_id"], task["id"])
        else:
            raise RuntimeError(f"不支持的任务类型: {task['task_type']}")
        await asyncio.wait_for(operation, timeout=task["timeout_seconds"])
        logger.info(
            "Document task completed",
            extra={
                "event": "document.task.completed",
                "task_id": str(task["id"]),
                "task_type": task["task_type"],
                "worker_id": worker_id,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "result": "completed",
            },
        )
        return "completed"
    except TimeoutError:
        logger.exception(
            "Document task timed out",
            extra={
                "event": "document.task.timeout",
                "task_id": str(task["id"]),
                "task_type": task["task_type"],
                "worker_id": worker_id,
            },
        )
        return await mark_failure(task, "TASK_TIMEOUT", "文档任务执行超时")
    except Exception as exc:
        logger.exception(
            "Document task failed",
            extra={
                "event": "document.task.failed",
                "task_id": str(task["id"]),
                "task_type": task["task_type"],
                "worker_id": worker_id,
            },
        )
        return await mark_failure(task, "DOCUMENT_TASK_FAILED", str(exc))
    finally:
        keep_alive.cancel()
        try:
            await keep_alive
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception(
                "Document task heartbeat failed",
                extra={
                    "event": "document.task.heartbeat_failed",
                    "task_id": str(task["id"]),
                    "worker_id": worker_id,
                },
            )


async def run_once(worker_id: str) -> bool:
    await recover_stale_tasks()
    task = await claim_next_task(worker_id)
    if not task:
        return False
    await execute_task(task, worker_id)
    return True
