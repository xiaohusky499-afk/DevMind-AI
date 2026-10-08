"""高频 FAQ 接口：列表、详情、用户问题缓存查询。"""
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..core.responses import ok
from ..core.security import get_current_user
from ..schemas import FAQSearchRequest
from ..services.knowledge_service import get_faq, list_faq, search_faq_question

router = APIRouter(prefix="/faq", tags=["高频问答"])


@router.get("")
async def faq_list(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    keyword: str | None = None,
    category: str | None = None,
    team: str | None = None,
):
    """分页查看高频问答对。"""
    return ok(
        request,
        await list_faq(current_user, page, page_size, keyword, category, team),
    )


@router.get("/{faq_id}")
async def faq_detail(
    faq_id: int,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """查看单个 FAQ 详情。"""
    return ok(request, await get_faq(faq_id, current_user))


@router.post("/search")
async def faq_search(
    req: FAQSearchRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """用户问题查询。

    流程：
    1. Redis 单独 db 2 缓存用户问题
    2. 缓存命中直接返回
    3. 缓存未命中查 PostgreSQL，查不到写空缓存，防止穿透
    4. 热点问题用 Redis 锁 + 指数退避，防止缓存击穿
    """
    return ok(request, await search_faq_question(req.question, current_user))
