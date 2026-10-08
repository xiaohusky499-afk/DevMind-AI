"""GET /api/client/bootstrap：客户端启动配置。"""
from fastapi import APIRouter, Request

from ..core.responses import ok

router = APIRouter()


@router.get("/client/bootstrap")
def bootstrap(request: Request):
    # TODO(T12 后端组): announcements / help_links / features。
    return ok(request, {"announcements": [], "help_links": [], "features": {}})
