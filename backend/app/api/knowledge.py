"""知识库、成员权限与文档接口。"""

import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi import (
    status as http_status,
)

from ..core.responses import ok
from ..core.security import get_current_user
from ..schemas import (
    DocumentReindexRequest,
    DocumentUpdateRequest,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseMemberRequest,
    KnowledgeBaseUpdateRequest,
)
from ..services import document_service, knowledge_base_service
from ..services.knowledge_service import get_document, list_documents

router = APIRouter(prefix="/knowledge", tags=["知识库"])
knowledge_base_router = APIRouter(prefix="/knowledge-bases", tags=["知识库"])
document_router = APIRouter(prefix="/documents", tags=["文档管理"])


@router.get("/docs")
async def docs(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: str | None = None,
    doc_type: str | None = None,
    team: str | None = None,
):
    """分页查看知识库文档。"""
    return ok(
        request,
        await list_documents(current_user, page, page_size, keyword, doc_type, team),
    )


@router.get("/docs/{doc_id}")
async def doc_detail(
    doc_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """查看单个文档详情。"""
    return ok(request, await get_document(doc_id, current_user))


@knowledge_base_router.get("")
async def knowledge_bases(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    keyword: str | None = None,
    status: Literal["creating", "ready", "indexing", "failed", "disabled"]
    | None = None,
    cursor: UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    return ok(
        request,
        await knowledge_base_service.list_knowledge_bases(
            current_user, keyword, status, cursor, limit
        ),
    )


@knowledge_base_router.post("")
async def create_knowledge_base(
    req: KnowledgeBaseCreateRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await knowledge_base_service.create_knowledge_base(
            req.model_dump(), current_user, request.state.request_id
        ),
    )


@knowledge_base_router.get("/{knowledge_base_id}")
async def knowledge_base_detail(
    knowledge_base_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await knowledge_base_service.get_knowledge_base(
            knowledge_base_id, current_user
        ),
    )


@knowledge_base_router.patch("/{knowledge_base_id}")
async def update_knowledge_base(
    knowledge_base_id: UUID,
    req: KnowledgeBaseUpdateRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await knowledge_base_service.update_knowledge_base(
            knowledge_base_id,
            req.model_dump(exclude_unset=True, exclude_none=True),
            current_user,
            request.state.request_id,
        ),
    )


@knowledge_base_router.delete("/{knowledge_base_id}")
async def archive_knowledge_base(
    knowledge_base_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await knowledge_base_service.archive_knowledge_base(
            knowledge_base_id, current_user, request.state.request_id
        ),
    )


@knowledge_base_router.get("/{knowledge_base_id}/members")
async def knowledge_base_members(
    knowledge_base_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await knowledge_base_service.list_members(knowledge_base_id, current_user),
    )


@knowledge_base_router.put("/{knowledge_base_id}/members/{user_id}")
async def set_knowledge_base_member(
    knowledge_base_id: UUID,
    user_id: UUID,
    req: KnowledgeBaseMemberRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await knowledge_base_service.set_member_role(
            knowledge_base_id,
            user_id,
            req.role,
            current_user,
            request.state.request_id,
        ),
    )


@knowledge_base_router.delete("/{knowledge_base_id}/members/{user_id}")
async def delete_knowledge_base_member(
    knowledge_base_id: UUID,
    user_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await knowledge_base_service.remove_member(
            knowledge_base_id,
            user_id,
            current_user,
            request.state.request_id,
        ),
    )


@document_router.get("")
async def documents(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    source_type: str | None = None,
):
    return ok(
        request,
        await list_documents(current_user, page, page_size, keyword, source_type, None),
    )


@document_router.post("", status_code=http_status.HTTP_202_ACCEPTED)
async def upload_document(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    file: Annotated[UploadFile, File()],
    knowledge_base_id: Annotated[UUID, Form()],
    display_name: Annotated[str | None, Form()] = None,
    metadata: Annotated[str | None, Form()] = None,
):
    try:
        parsed_metadata = json.loads(metadata) if metadata else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_METADATA", "message": "metadata 必须是 JSON 对象"},
        ) from exc
    if not isinstance(parsed_metadata, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_METADATA", "message": "metadata 必须是 JSON 对象"},
        )
    return ok(
        request,
        await document_service.upload_document(
            file,
            knowledge_base_id,
            display_name,
            parsed_metadata,
            current_user,
            request.state.request_id,
        ),
    )


@document_router.get("/{document_id}")
async def document_detail(
    document_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(request, await get_document(document_id, current_user))


@document_router.patch("/{document_id}")
async def patch_document(
    document_id: UUID,
    req: DocumentUpdateRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await document_service.update_document(
            document_id,
            req.model_dump(exclude_unset=True, exclude_none=True),
            current_user,
            request.state.request_id,
        ),
    )


@document_router.delete("/{document_id}", status_code=http_status.HTTP_202_ACCEPTED)
async def remove_document(
    document_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await document_service.delete_document(
            document_id,
            current_user,
            request.state.request_id,
        ),
    )


@document_router.post(
    "/{document_id}/reindex", status_code=http_status.HTTP_202_ACCEPTED
)
async def reindex_document(
    document_id: UUID,
    req: DocumentReindexRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await document_service.reindex_document(
            document_id,
            req.force,
            current_user,
            request.state.request_id,
        ),
    )


@document_router.post(
    "/{document_id}/task/retry", status_code=http_status.HTTP_202_ACCEPTED
)
async def retry_document_task(
    document_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await document_service.retry_document_task(
            document_id, current_user, request.state.request_id
        ),
    )


@document_router.get("/{document_id}/chunks")
async def document_chunks(
    document_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    cursor: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    return ok(
        request,
        await document_service.list_chunks(document_id, current_user, cursor, limit),
    )


@document_router.get("/{document_id}/task")
async def document_task(
    document_id: UUID,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(request, await document_service.task_status(document_id, current_user))
