"""后端 API 路由聚合。"""

from fastapi import APIRouter

from .auth import router as auth_router
from .bootstrap import router as bootstrap_router
from .chat import router as chat_router
from .faq import router as faq_router
from .knowledge import document_router, knowledge_base_router
from .knowledge import router as knowledge_router
from .user import router as user_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(bootstrap_router)
api_router.include_router(chat_router)
api_router.include_router(auth_router)
api_router.include_router(user_router)
api_router.include_router(knowledge_router)
api_router.include_router(knowledge_base_router)
api_router.include_router(document_router)
api_router.include_router(faq_router)
