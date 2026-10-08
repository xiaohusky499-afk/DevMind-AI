"""T8 API 路由注册。"""
from fastapi import APIRouter

from . import feedback, health, query, session, sources, stream

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(session.router)
api_router.include_router(query.router)
api_router.include_router(stream.router)
api_router.include_router(sources.router)
api_router.include_router(feedback.router)
