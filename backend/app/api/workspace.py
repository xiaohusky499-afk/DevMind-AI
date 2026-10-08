"""Runnable development API for the DevMind AI workspace.

ponytail: in-memory store for local UI integration; replace WorkspaceStore with
PostgreSQL/Milvus repositories when persistence and multi-process deployment matter.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1", tags=["workspace"])


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ok(data: Any) -> dict[str, Any]:
    return {"request_id": f"req_{uuid4().hex[:12]}", "data": data, "error": None}


class SessionIn(BaseModel):
    title: str = ""
    knowledge_base_ids: list[str] = Field(default_factory=list)


class MessageIn(BaseModel):
    session_id: str
    content: str = Field(min_length=1, max_length=20_000)
    mode: Literal["tech", "troubleshoot", "summarize"] = "tech"
    knowledge_base_ids: list[str] = Field(default_factory=list)
    parent_message_id: str | None = None


class PreferencesIn(BaseModel):
    default_knowledge_base_ids: list[str] = Field(default_factory=list)
    default_mode: Literal["tech", "troubleshoot", "summarize"] = "tech"
    locale: str = "zh-CN"


class RetrievalIn(BaseModel):
    query: str = Field(min_length=1, max_length=20_000)
    knowledge_base_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=20, ge=1, le=100)
    rerank_top_n: int = Field(default=5, ge=1, le=20)


class Store:
    knowledge_bases = [
        {"id": "kb_platform", "name": "平台工程知识库", "document_count": 128, "status": "ready", "updated_at": now(), "role": "admin"},
        {"id": "kb_security", "name": "安全与合规知识库", "document_count": 86, "status": "ready", "updated_at": now(), "role": "editor"},
        {"id": "kb_engineering", "name": "研发实践知识库", "document_count": 214, "status": "ready", "updated_at": now(), "role": "viewer"},
    ]
    documents = [
        {"id": "doc_auth", "knowledge_base_id": "kb_security", "name": "身份认证接入规范.pdf", "source_type": "pdf", "size_bytes": 1_240_000, "status": "ready", "created_at": now(), "error_message": None},
        {"id": "doc_deploy", "knowledge_base_id": "kb_platform", "name": "生产环境部署手册.md", "source_type": "md", "size_bytes": 85_000, "status": "ready", "created_at": now(), "error_message": None},
    ]
    sessions: dict[str, dict[str, Any]] = {}
    messages: dict[str, dict[str, Any]] = {}
    favorites: set[str] = set()
    preferences = {"default_knowledge_base_ids": ["kb_platform", "kb_security"], "default_mode": "tech", "locale": "zh-CN"}


store = Store()


def assistant_text() -> str:
    return "Milvus 适合企业级 RAG：分布式架构支持弹性扩展；高性能向量检索可满足大规模低延迟查询；存储与计算解耦，便于多租户隔离与长期演进。"


def citations(message_id: str) -> list[dict[str, Any]]:
    return [
        {"id": "cite_auth", "message_id": message_id, "document_id": "doc_auth", "document_name": "身份认证接入规范.pdf", "document_type": "pdf", "location_label": "第 18 页", "chunk_id": "chunk_auth_18", "score": 0.94, "excerpt": "OIDC 接入应统一使用企业 IdP，并结合 MFA 与角色…", "url": None},
        {"id": "cite_deploy", "message_id": message_id, "document_id": "doc_deploy", "document_name": "生产环境部署手册.md", "document_type": "md", "location_label": "Chunk #42", "chunk_id": "chunk_deploy_42", "score": 0.91, "excerpt": "生产环境需启用权限映射与审计日志…", "url": None},
    ]


@router.get("/knowledge-bases")
def list_knowledge_bases() -> dict[str, Any]:
    return ok(store.knowledge_bases)


@router.get("/documents")
def list_documents(knowledge_base_id: str | None = None) -> dict[str, Any]:
    rows = [d for d in store.documents if not knowledge_base_id or d["knowledge_base_id"] == knowledge_base_id]
    return ok({"items": rows, "next_cursor": None})


@router.post("/sessions")
def create_session(body: SessionIn) -> dict[str, Any]:
    session_id = f"sess_{uuid4().hex[:12]}"
    row = {"id": session_id, "title": body.title or "新建对话", "knowledge_base_ids": body.knowledge_base_ids, "messages": [], "updated_at": now(), "created_at": now()}
    store.sessions[session_id] = row
    return ok({k: v for k, v in row.items() if k != "messages"})


@router.get("/sessions")
def list_sessions() -> dict[str, Any]:
    rows = [{k: v for k, v in s.items() if k != "messages"} for s in store.sessions.values()]
    return ok({"items": sorted(rows, key=lambda x: x["updated_at"], reverse=True), "next_cursor": None})


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    row = store.sessions.get(session_id)
    if not row:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    return ok(row)


@router.post("/messages")
def create_message(body: MessageIn) -> dict[str, Any]:
    session = store.sessions.get(body.session_id)
    if not session:
        raise HTTPException(404, "SESSION_NOT_FOUND")
    user_id, assistant_id = f"msg_{uuid4().hex[:12]}", f"msg_{uuid4().hex[:12]}"
    user = {"id": user_id, "role": "user", "content": body.content, "created_at": now()}
    assistant = {"id": assistant_id, "role": "assistant", "content": "", "mode": body.mode, "status": "pending", "citation_count": 0, "confidence": None, "created_at": now()}
    store.messages[user_id], store.messages[assistant_id] = user, assistant
    session["messages"].extend([user, assistant]); session["updated_at"] = now()
    return ok({"user_message_id": user_id, "assistant_message_id": assistant_id, "stream_url": f"/api/v1/chat/stream?message_id={assistant_id}"})


@router.get("/messages/{message_id}")
def get_message(message_id: str) -> dict[str, Any]:
    row = store.messages.get(message_id)
    if not row:
        raise HTTPException(404, "MESSAGE_NOT_FOUND")
    return ok(row)


@router.websocket("/chat/stream")
async def stream_chat(websocket: WebSocket, message_id: str) -> None:
    await websocket.accept()
    message = store.messages.get(message_id)
    if not message:
        await websocket.send_json({"type": "error", "data": {"code": "MESSAGE_NOT_FOUND", "message": "消息不存在"}})
        await websocket.close(code=4404)
        return
    try:
        for stage, text in [("analyzing", "正在分析问题…"), ("search", "正在检索 12 个知识库…"), ("recall", "已找到 13 个相关片段"), ("rerank", "正在重新排序…"), ("generating", "正在生成回答…")]:
            await websocket.send_json({"type": "status" if stage == "analyzing" else "retrieval", "data": {"stage": stage, "message": text, "retrieved_count": 13, "reranked_count": 5}})
            await asyncio.sleep(0.15)
        text = assistant_text()
        for offset in range(0, len(text), 18):
            token = text[offset:offset + 18]; message["content"] += token
            await websocket.send_json({"type": "token", "data": {"content": token}})
            await asyncio.sleep(0.02)
        message.update({"status": "completed", "confidence": "high", "citation_count": 2, "completed_at": now()})
        await websocket.send_json({"type": "citations", "data": {"items": citations(message_id)}})
        await websocket.send_json({"type": "final", "data": {"message_id": message_id, "confidence": "high", "citation_count": 2, "usage": {"input_tokens": 0, "output_tokens": 0}}})
    except WebSocketDisconnect:
        return
    finally:
        await websocket.close()


@router.get("/messages/{message_id}/citations")
def get_citations(message_id: str) -> dict[str, Any]:
    if message_id not in store.messages:
        raise HTTPException(404, "MESSAGE_NOT_FOUND")
    return ok({"answer_basis_count": 2, "confidence": "high", "items": citations(message_id)})


@router.get("/citations/{citation_id}")
def get_citation(citation_id: str) -> dict[str, Any]:
    item = next((c for c in citations("msg") if c["id"] == citation_id), None)
    if not item:
        raise HTTPException(404, "CITATION_NOT_FOUND")
    return ok({**item, "content": item["excerpt"], "highlights": [], "access_allowed": True})


@router.put("/favorites/messages/{message_id}")
def favorite_message(message_id: str) -> dict[str, Any]:
    if message_id not in store.messages:
        raise HTTPException(404, "MESSAGE_NOT_FOUND")
    store.favorites.add(message_id)
    return ok({"message_id": message_id, "favorited": True})


@router.delete("/favorites/messages/{message_id}")
def unfavorite_message(message_id: str) -> dict[str, Any]:
    store.favorites.discard(message_id)
    return ok({"message_id": message_id, "favorited": False})


@router.get("/favorites")
def list_favorites() -> dict[str, Any]:
    return ok({"items": [store.messages[m] for m in store.favorites if m in store.messages], "next_cursor": None})


@router.get("/me")
def get_me() -> dict[str, Any]:
    return ok({"id": "user_demo", "display_name": "张工程师", "department": "研发部", "email": "zhang@example.com"})


@router.get("/me/preferences")
def get_preferences() -> dict[str, Any]:
    return ok(store.preferences)


@router.patch("/me/preferences")
def update_preferences(body: PreferencesIn) -> dict[str, Any]:
    store.preferences = body.model_dump()
    return ok(store.preferences)


@router.post("/retrieval/test")
def retrieval_test(body: RetrievalIn) -> dict[str, Any]:
    results = [{"chunk_id": "chunk_auth_18", "document_name": "身份认证接入规范.pdf", "location_label": "第 18 页", "stage": "rerank", "rank": 1, "score": 0.94, "excerpt": "OIDC 接入应统一使用企业 IdP…"}, {"chunk_id": "chunk_deploy_42", "document_name": "生产环境部署手册.md", "location_label": "Chunk #42", "stage": "rerank", "rank": 2, "score": 0.91, "excerpt": "生产环境需启用权限映射与审计日志…"}]
    return ok({"query": body.query, "rewritten_query": f"{body.query} 的企业技术规范", "retrieved_count": 13, "reranked_count": min(body.rerank_top_n, len(results)), "results": results})
