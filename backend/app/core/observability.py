"""结构化日志与轻量 Prometheus 指标。"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
from collections import defaultdict
from datetime import UTC, datetime

from internal_kb_qa.core.audit_logger import mask_text

_context: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "devmind_log_context", default=None
)
_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_lock = threading.Lock()
_requests: dict[tuple[str, str, int], int] = defaultdict(int)
_latency_count: dict[tuple[str, str], int] = defaultdict(int)
_latency_sum: dict[tuple[str, str], float] = defaultdict(float)
_latency_buckets: dict[tuple[str, str, float], int] = defaultdict(int)
_in_progress = 0


def set_request_context(request_id: str, method: str, path: str):
    return _context.set({"request_id": request_id, "method": method, "path": path})


def bind_log_context(**values) -> None:
    _context.set(
        {**(_context.get() or {}), **{key: str(value) for key, value in values.items()}}
    )


def reset_request_context(token) -> None:
    _context.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event", record.name),
            "logger": record.name,
            "message": mask_text(record.getMessage()),
            **(_context.get() or {}),
        }
        for field in (
            "status_code",
            "duration_ms",
            "worker_id",
            "task_id",
            "task_type",
            "result",
            "retrieved_count",
            "reranked_count",
            "citation_count",
            "confidence",
        ):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = mask_text(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True


def request_started() -> None:
    global _in_progress
    with _lock:
        _in_progress += 1


def request_finished(
    method: str, route: str, status_code: int, duration: float, *, record: bool = True
) -> None:
    global _in_progress
    key = (method, route)
    with _lock:
        _in_progress = max(0, _in_progress - 1)
        if not record:
            return
        _requests[(method, route, status_code)] += 1
        _latency_count[key] += 1
        _latency_sum[key] += duration
        for bucket in _BUCKETS:
            if duration <= bucket:
                _latency_buckets[(method, route, bucket)] += 1


def _labels(**values) -> str:
    escaped = [f'{key}="{str(value).replace(chr(34), chr(92) + chr(34))}"' for key, value in values.items()]
    return "{" + ",".join(escaped) + "}"


def render_http_metrics() -> str:
    with _lock:
        requests = dict(_requests)
        counts = dict(_latency_count)
        sums = dict(_latency_sum)
        buckets = dict(_latency_buckets)
        in_progress = _in_progress
    lines = [
        "# HELP devmind_http_requests_total HTTP requests by route and status.",
        "# TYPE devmind_http_requests_total counter",
    ]
    for (method, route, status), value in sorted(requests.items()):
        lines.append(
            f"devmind_http_requests_total{_labels(method=method, route=route, status=status)} {value}"
        )
    lines.extend(
        [
            "# HELP devmind_http_request_duration_seconds HTTP request latency.",
            "# TYPE devmind_http_request_duration_seconds histogram",
        ]
    )
    for method, route in sorted(counts):
        for bucket in _BUCKETS:
            value = buckets.get((method, route, bucket), 0)
            lines.append(
                f"devmind_http_request_duration_seconds_bucket{_labels(method=method, route=route, le=bucket)} {value}"
            )
        lines.append(
            f"devmind_http_request_duration_seconds_bucket{_labels(method=method, route=route, le='+Inf')} {counts[(method, route)]}"
        )
        lines.append(
            f"devmind_http_request_duration_seconds_sum{_labels(method=method, route=route)} {sums[(method, route)]:.6f}"
        )
        lines.append(
            f"devmind_http_request_duration_seconds_count{_labels(method=method, route=route)} {counts[(method, route)]}"
        )
    lines.extend(
        [
            "# HELP devmind_http_requests_in_progress Current HTTP requests.",
            "# TYPE devmind_http_requests_in_progress gauge",
            f"devmind_http_requests_in_progress {in_progress}",
        ]
    )
    return "\n".join(lines) + "\n"


async def render_operational_metrics() -> str:
    """从 PostgreSQL 导出可恢复的 Worker 与 RAG 状态。"""
    from .db import db

    task_rows = await db.fetch_all(
        "SELECT status, count(*)::int AS count FROM document_tasks GROUP BY status"
    )
    stale = await db.fetch_one(
        """SELECT count(*)::int AS count FROM document_tasks
           WHERE status='running' AND lease_expires_at < now()"""
    )
    rag = await db.fetch_one(
        """SELECT count(*)::int AS runs,
                  COALESCE(avg(latency_ms),0)::float8 AS latency_avg_ms,
                  COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms),0)::float8
                    AS latency_p95_ms
           FROM retrieval_runs WHERE created_at >= now()-interval '5 minutes'"""
    )
    failed_answers = await db.fetch_one(
        """SELECT count(*)::int AS count FROM chat_messages
           WHERE role='assistant' AND status='failed'
             AND created_at >= now()-interval '15 minutes'"""
    )
    lines = [
        "# HELP devmind_document_tasks Current durable document tasks by status.",
        "# TYPE devmind_document_tasks gauge",
    ]
    for row in sorted(task_rows, key=lambda item: item["status"]):
        lines.append(
            f"devmind_document_tasks{_labels(status=row['status'])} {row['count']}"
        )
    lines.extend(
        [
            "# HELP devmind_document_stale_tasks Running tasks with an expired lease.",
            "# TYPE devmind_document_stale_tasks gauge",
            f"devmind_document_stale_tasks {stale['count']}",
            "# HELP devmind_rag_retrieval_runs_5m Retrieval runs created in five minutes.",
            "# TYPE devmind_rag_retrieval_runs_5m gauge",
            f"devmind_rag_retrieval_runs_5m {rag['runs']}",
            "# HELP devmind_rag_retrieval_latency_ms Retrieval latency in five minutes.",
            "# TYPE devmind_rag_retrieval_latency_ms gauge",
            f"devmind_rag_retrieval_latency_ms{_labels(stat='avg')} {rag['latency_avg_ms']:.3f}",
            f"devmind_rag_retrieval_latency_ms{_labels(stat='p95')} {rag['latency_p95_ms']:.3f}",
            "# HELP devmind_rag_failed_answers_15m Failed AI answers in fifteen minutes.",
            "# TYPE devmind_rag_failed_answers_15m gauge",
            f"devmind_rag_failed_answers_15m {failed_answers['count']}",
        ]
    )
    return "\n".join(lines) + "\n"
