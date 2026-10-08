"""统一 API 响应与异常。"""
from __future__ import annotations

from typing import Any

from fastapi import Request
from pydantic import BaseModel


def request_id(request: Request) -> str:
    return request.state.request_id


def ok(request: Request, data: Any) -> dict[str, Any]:
    if isinstance(data, BaseModel):
        data = data.model_dump(mode="json")
    return {"request_id": request_id(request), "data": data, "error": None}


def error(request: Request, code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {
        "request_id": request_id(request),
        "data": None,
        "error": {"code": code, "message": message, "details": details},
    }
