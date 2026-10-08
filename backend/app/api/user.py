"""用户信息接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..core.responses import ok
from ..core.security import get_current_user
from ..schemas import PreferencesUpdateRequest, ProfileUpdateRequest
from ..services import user_service, workspace_service

router = APIRouter(tags=["用户"])


@router.get("/organization/users")
async def organization_users(
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
    keyword: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    """供知识库管理员选择同组织成员。"""
    return ok(
        request,
        await user_service.list_organization_users(current_user, keyword, limit),
    )


@router.get("/me")
async def me(
    request: Request, current_user: Annotated[dict, Depends(get_current_user)]
):
    """获取当前登录用户信息。"""
    return ok(
        request,
        {
            "id": current_user["id"],
            "username": current_user["username"],
            "nickname": current_user["nickname"],
            "phone": current_user["phone"],
            "team": current_user["team"],
            "security_level": current_user["security_level"],
        },
    )


@router.get("/me/profile")
async def get_profile(
    request: Request, current_user: Annotated[dict, Depends(get_current_user)]
):
    """读取当前用户的完整档案和界面偏好。"""
    return ok(request, await user_service.get_profile(current_user))


@router.patch("/me/profile")
async def update_profile(
    req: ProfileUpdateRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """只更新当前用户可编辑的档案字段。"""
    return ok(
        request,
        await user_service.update_profile(
            current_user,
            req.model_dump(exclude_unset=True),
            request.state.request_id,
        ),
    )


@router.get("/me/preferences")
async def get_preferences(
    request: Request, current_user: Annotated[dict, Depends(get_current_user)]
):
    return ok(request, await workspace_service.get_preferences(current_user))


@router.patch("/me/preferences")
async def update_preferences(
    req: PreferencesUpdateRequest,
    request: Request,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return ok(
        request,
        await workspace_service.update_preferences(
            req.model_dump(exclude_unset=True, exclude_none=True),
            current_user,
            request.state.request_id,
        ),
    )
