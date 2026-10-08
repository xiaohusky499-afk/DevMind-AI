"""WS /api/stream（流式 RAG 主链路）"""
from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/api/stream")
async def stream(websocket: WebSocket):
    # TODO(T8 服务组): start/token/end/error 消息契约，token 逐片下发。
    await websocket.accept()
    raise NotImplementedError
