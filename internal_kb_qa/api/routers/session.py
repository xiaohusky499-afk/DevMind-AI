"""POST /api/create_session"""
from fastapi import APIRouter

router = APIRouter()


@router.post("/api/create_session")
def create_session():
    # TODO(T8 服务组): 返回 UUID session_id，并初始化 Redis 会话。
    raise NotImplementedError
