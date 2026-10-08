"""正式智能问答、会话、检索和引用接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from ..core.responses import ok
from ..core.security import get_current_user
from ..schemas import (
    MessageCreateRequest,
    RetrievalTestRequest,
    SessionCreateRequest,
    SessionUpdateRequest,
)
from ..services import rag_service, workspace_service

router = APIRouter(tags=["智能问答"])


@router.post("/sessions")
async def create_session(
    req: SessionCreateRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await rag_service.create_session(req.model_dump(), current_user),
    )


@router.get("/sessions")
async def sessions(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    cursor: UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
):
    return ok(
        request,
        await rag_service.list_sessions(current_user, cursor, limit, keyword),
    )


@router.get("/sessions/{session_id}")
async def session_detail(
    session_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(request, await rag_service.get_session(session_id, current_user))


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: UUID,
    req: SessionUpdateRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await rag_service.update_session(
            session_id, req.title, current_user, request.state.request_id
        ),
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await rag_service.delete_session(
            session_id, current_user, request.state.request_id
        ),
    )


@router.get("/favorites")
async def favorites(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    cursor: UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
):
    return ok(
        request,
        await workspace_service.list_favorites(current_user, cursor, limit, keyword),
    )


@router.put("/favorites/messages/{message_id}")
async def favorite_message(
    message_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await workspace_service.set_favorite(
            message_id, True, current_user, request.state.request_id
        ),
    )


@router.delete("/favorites/messages/{message_id}")
async def unfavorite_message(
    message_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await workspace_service.set_favorite(
            message_id, False, current_user, request.state.request_id
        ),
    )


@router.post("/messages")
async def create_message(
    req: MessageCreateRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await rag_service.create_message(req.model_dump(), current_user),
    )


@router.get("/messages/{message_id}")
async def message_detail(
    message_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(request, await rag_service.get_message(message_id, current_user))


@router.get("/messages/{message_id}/citations")
async def citations(
    message_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(request, await rag_service.message_citations(message_id, current_user))


@router.get("/citations/{citation_id}")
async def citation(
    citation_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(request, await rag_service.citation_detail(citation_id, current_user))


@router.post("/retrieval/test")
async def retrieval_test(
    req: RetrievalTestRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    result = await rag_service.retrieve(
        req.query,
        current_user,
        req.knowledge_base_ids,
        req.top_k,
        req.rerank_top_n,
    )
    return ok(request, rag_service.retrieval_payload(result))


@router.websocket("/chat/stream")
async def chat_stream(websocket: WebSocket, message_id: UUID, ticket: str):
    try:
        current_user = await rag_service.consume_stream_ticket(ticket, message_id)
    except Exception:  # noqa: BLE001 -- WebSocket 握手统一关闭，不暴露鉴权细节
        await websocket.close(code=4401, reason="登录无效")
        return
    await websocket.accept()
    try:
        async for event in rag_service.answer_events(message_id, current_user):
            await websocket.send_json(jsonable_encoder(event))
    except (WebSocketDisconnect, OSError):
        await rag_service.mark_stream_interrupted(message_id)
    finally:
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect, OSError):
            pass
