"""POST /api/feedback：点赞点踩、评论、转人工。"""
from fastapi import APIRouter

router = APIRouter()


@router.post("/api/feedback")
def feedback(payload: dict):
    # TODO(T8 服务组): need_human=true 时创建工单并返回 ticket_id；反馈回写知识库。
    raise NotImplementedError
