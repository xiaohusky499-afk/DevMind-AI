"""响应模型：QueryResponse、Source、RAGResult、FeedbackResponse。"""
from pydantic import BaseModel


class Source(BaseModel):
    title: str
    page: int | None = None
    score: float
    text: str


class QueryResponse(BaseModel):
    answer: str
    is_streaming: bool
    need_human: bool
    sources: list[Source] = []
    session_id: str
    processing_time: float


class FeedbackResponse(BaseModel):
    status: str
    ticket_id: str | None = None
