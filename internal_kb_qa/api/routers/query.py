"""POST /api/query（非流式：闲聊 / FAQ 命中 / 兜底转人工）"""
from fastapi import APIRouter

router = APIRouter()


@router.post("/api/query")
def query(payload: dict):
    # TODO(T8 服务组): 串联 T5/T6/T7；返回 answer/is_streaming/need_human/sources。
    raise NotImplementedError
