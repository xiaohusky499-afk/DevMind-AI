"""请求模型：QueryRequest、StreamRequest、FeedbackRequest。"""
from pydantic import BaseModel


class QueryRequest(BaseModel):
    query: str
    session_id: str | None = None
    source_filter: str | None = None


class FeedbackRequest(BaseModel):
    session_id: str
    query: str
    rating: int
    comment: str | None = None
    need_human: bool | None = False
