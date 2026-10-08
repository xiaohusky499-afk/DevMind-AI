"""GET /health"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    # TODO(T8 服务组): 可扩展 MySQL/Redis/Milvus 依赖检查。
    return {"status": "healthy"}
