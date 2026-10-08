"""后端正式入口的最小契约检查。"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from backend.app.api.chat import router as chat_router
from backend.app.core.config import settings
from backend.app.core.db import db
from backend.app.main import app
from backend.app.schemas import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseMemberRequest,
    KnowledgeBaseUpdateRequest,
    MessageCreateRequest,
    PreferencesUpdateRequest,
    ProfileUpdateRequest,
    RetrievalTestRequest,
    SessionCreateRequest,
    SessionUpdateRequest,
)
from backend.app.services import document_task_service, rag_service
from backend.app.services.rag_service import retrieval_payload
from internal_kb_qa.core.hit import Hit


def test_v1_routes_are_registered_without_workspace_mock() -> None:
    paths = set(app.openapi()["paths"])

    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/login/sms" in paths
    assert "/api/v1/me" in paths
    assert "/api/v1/knowledge/docs" in paths
    assert "/api/v1/faq/search" in paths
    assert "/api/v1/sessions" in paths
    assert "/api/auth/login" not in paths


def test_cors_does_not_use_wildcard_with_credentials() -> None:
    cors = next(
        middleware
        for middleware in app.user_middleware
        if middleware.cls.__name__ == "CORSMiddleware"
    )
    assert "*" not in cors.kwargs["allow_origins"]


def test_postgresql_is_the_runtime_database() -> None:
    assert settings.POSTGRES_PORT == 15432
    assert settings.POSTGRES_DB == "internal_tech_kb"
    assert hasattr(db, "postgres_pool")
    assert not hasattr(db, "mysql_pool")
    assert settings.RAG_LOCAL_MODEL_CONCURRENCY >= 1


def test_readiness_route_is_registered() -> None:
    assert "/health/ready" in app.openapi()["paths"]


def test_profile_routes_are_registered() -> None:
    operations = app.openapi()["paths"]["/api/v1/me/profile"]
    assert {"get", "patch"} <= operations.keys()


def test_profile_update_validates_dates_phone_and_timezone() -> None:
    valid = ProfileUpdateRequest(
        display_name="张工程师",
        phone="+8613800138000",
        timezone="Asia/Shanghai",
    )
    assert valid.display_name == "张工程师"

    with pytest.raises(ValidationError):
        ProfileUpdateRequest(birth_date=(datetime.now(UTC) + timedelta(days=1)).date())
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(phone="13800138000")
    with pytest.raises(ValidationError):
        ProfileUpdateRequest(timezone="invalid-timezone")


def test_knowledge_base_routes_are_registered() -> None:
    paths = app.openapi()["paths"]
    assert {"get", "post"} <= paths["/api/v1/knowledge-bases"].keys()
    assert {"get", "patch", "delete"} <= paths[
        "/api/v1/knowledge-bases/{knowledge_base_id}"
    ].keys()
    assert {"get"} <= paths[
        "/api/v1/knowledge-bases/{knowledge_base_id}/members"
    ].keys()
    assert {"put", "delete"} <= paths[
        "/api/v1/knowledge-bases/{knowledge_base_id}/members/{user_id}"
    ].keys()


def test_knowledge_base_payload_validation() -> None:
    assert KnowledgeBaseCreateRequest(name="  平台知识库  ").name == "平台知识库"
    assert KnowledgeBaseUpdateRequest(status="disabled").status == "disabled"
    assert KnowledgeBaseMemberRequest(role="editor").role == "editor"
    with pytest.raises(ValidationError):
        KnowledgeBaseMemberRequest(role="owner")


def test_document_write_routes_are_registered() -> None:
    paths = app.openapi()["paths"]
    assert {"get", "post"} <= paths["/api/v1/documents"].keys()
    assert {"get", "patch", "delete"} <= paths["/api/v1/documents/{document_id}"].keys()
    assert {"post"} <= paths["/api/v1/documents/{document_id}/reindex"].keys()
    assert {"get"} <= paths["/api/v1/documents/{document_id}/chunks"].keys()
    assert {"get"} <= paths["/api/v1/documents/{document_id}/task"].keys()
    assert {"post"} <= paths["/api/v1/documents/{document_id}/task/retry"].keys()


def test_document_task_retry_delay_is_exponential_and_bounded() -> None:
    base = settings.DOCUMENT_TASK_RETRY_BASE_SECONDS
    assert document_task_service.retry_delay_seconds(1) == base
    assert document_task_service.retry_delay_seconds(2) == base * 2
    assert document_task_service.retry_delay_seconds(100) == 900


def test_rag_routes_and_payloads_are_registered() -> None:
    paths = app.openapi()["paths"]
    assert {"get", "post"} <= paths["/api/v1/sessions"].keys()
    assert {"get", "patch", "delete"} <= paths["/api/v1/sessions/{session_id}"].keys()
    assert {"post"} <= paths["/api/v1/messages"].keys()
    assert {"get"} <= paths["/api/v1/messages/{message_id}"].keys()
    assert {"get"} <= paths["/api/v1/messages/{message_id}/citations"].keys()
    assert {"get"} <= paths["/api/v1/citations/{citation_id}"].keys()
    assert {"post"} <= paths["/api/v1/retrieval/test"].keys()
    assert {"get"} <= paths["/api/v1/favorites"].keys()
    assert {"put", "delete"} <= paths["/api/v1/favorites/messages/{message_id}"].keys()
    assert {"get", "patch"} <= paths["/api/v1/me/preferences"].keys()
    assert {"get"} <= paths["/api/v1/organization/users"].keys()
    assert any(route.path == "/chat/stream" for route in chat_router.routes)

    assert SessionCreateRequest().title == ""
    assert (
        MessageCreateRequest(
            session_id="00000000-0000-0000-0000-000000000001", content="  测试问题  "
        ).content
        == "测试问题"
    )
    assert RetrievalTestRequest(query="认证", top_k=10).top_k == 10
    assert SessionUpdateRequest(title="  新标题  ").title == "新标题"
    assert PreferencesUpdateRequest(answer_style="detailed").answer_style == "detailed"
    with pytest.raises(ValidationError):
        PreferencesUpdateRequest(default_mode="unknown")


def test_retrieval_payload_exposes_stage_timings() -> None:
    result = {
        "retrieval_run_id": "run-1",
        "query": "测试",
        "rewritten_query": "测试",
        "retrieved_count": 0,
        "reranked_count": 0,
        "latency_ms": 10,
        "timings": {"rewrite_ms": 4, "embedding_ms": 3, "milvus_ms": 1},
        "retrieved": [],
        "ranked": [],
    }

    assert retrieval_payload(result)["timings"]["embedding_ms"] == 3


def test_standalone_query_skips_llm_rewrite(monkeypatch) -> None:
    monkeypatch.setattr(
        rag_service,
        "query_rewrite",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError),
    )

    assert asyncio.run(rag_service._rewrite("Redis 连接超时", [])) == "Redis 连接超时"


def test_rerank_candidate_budget_keeps_at_least_three() -> None:
    hits = [Hit(text=str(index), score=1.0, metadata={}) for index in range(5)]

    assert len(rag_service._rerank_candidates(hits, 3)) == 3
    assert len(rag_service._rerank_candidates(hits, 5)) == 5
