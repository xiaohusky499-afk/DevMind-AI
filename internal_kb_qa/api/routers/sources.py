"""GET /api/sources"""
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/sources")
def sources():
    return {"sources": ["infra", "backend", "frontend", "data", "ops"]}
