import asyncio
import json
import logging

import httpx

from backend.app.core import observability


def test_json_log_contains_context_and_redacts_credentials() -> None:
    token = observability.set_request_context("req-test", "POST", "/login")
    try:
        record = logging.LogRecord(
            "devmind.test",
            logging.INFO,
            __file__,
            1,
            "Authorization: Bearer abc.def.ghi phone=13800138000",
            (),
            None,
        )
        record.event = "security.test"
        payload = json.loads(observability.JsonFormatter().format(record))
    finally:
        observability.reset_request_context(token)

    assert payload["request_id"] == "req-test"
    assert payload["event"] == "security.test"
    assert "abc.def.ghi" not in payload["message"]
    assert "13800138000" not in payload["message"]


def test_prometheus_http_metrics_use_bounded_route_labels() -> None:
    observability.request_started()
    observability.request_finished("GET", "/documents/{document_id}", 200, 0.2)

    rendered = observability.render_http_metrics()

    assert 'route="/documents/{document_id}"' in rendered
    assert 'status="200"' in rendered
    assert "devmind_http_request_duration_seconds_count" in rendered
    assert "devmind_http_requests_in_progress 0" in rendered


def test_operational_metrics_are_derived_from_durable_state(monkeypatch) -> None:
    async def fetch_all(_sql, _params=()):
        return [{"status": "dead", "count": 2}, {"status": "completed", "count": 7}]

    async def fetch_one(sql, _params=()):
        if "lease_expires_at" in sql:
            return {"count": 1}
        if "retrieval_runs" in sql:
            return {"runs": 3, "latency_avg_ms": 120.5, "latency_p95_ms": 210.0}
        return {"count": 4}

    from backend.app.core.db import db

    monkeypatch.setattr(db, "fetch_all", fetch_all)
    monkeypatch.setattr(db, "fetch_one", fetch_one)
    rendered = asyncio.run(observability.render_operational_metrics())

    assert 'devmind_document_tasks{status="dead"} 2' in rendered
    assert "devmind_document_stale_tasks 1" in rendered
    assert "devmind_rag_retrieval_runs_5m 3" in rendered
    assert "devmind_rag_failed_answers_15m 4" in rendered


def test_metrics_endpoint_requires_configured_token(monkeypatch) -> None:
    from backend.app import main

    async def operational():
        return "devmind_document_stale_tasks 0\n"

    async def exercise():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            denied = await client.get("/metrics")
            allowed = await client.get(
                "/metrics", headers={"Authorization": "Bearer metrics-test-token"}
            )
        return denied, allowed

    monkeypatch.setattr(main.settings, "METRICS_TOKEN", "metrics-test-token")
    monkeypatch.setattr(main, "render_operational_metrics", operational)
    denied, allowed = asyncio.run(exercise())

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert "devmind_metrics_collection_error 0" in allowed.text
