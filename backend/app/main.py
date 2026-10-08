"""DevMind-AI 后端服务入口。

启动方式：
    cd backend
    uvicorn app.main:app --host 0.0.0.0 --port 15200 --reload
"""
import hmac
import logging
import time
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import api_router
from .core.config import settings
from .core.db import db
from .core.observability import (
    configure_logging,
    render_http_metrics,
    render_operational_metrics,
    request_finished,
    request_started,
    reset_request_context,
    set_request_context,
)
from .core.responses import error

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.LOG_LEVEL)
    # 启动时创建 PostgreSQL/Redis 连接池
    await db.connect()
    yield
    # 关闭时释放连接池
    await db.close()


app = FastAPI(title="DevMind-AI Backend", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.middleware("http")
async def attach_request_id(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID", "").strip()
    request.state.request_id = incoming[:128] if incoming else f"req_{uuid.uuid4().hex}"
    token = set_request_context(
        request.state.request_id, request.method, request.url.path
    )
    started = time.perf_counter()
    request_started()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request.state.request_id
        return response
    finally:
        duration = time.perf_counter() - started
        route = getattr(request.scope.get("route"), "path", "__unmatched__")
        if request.url.path != "/metrics":
            request_finished(request.method, route, status_code, duration)
            logger.info(
                "HTTP request completed",
                extra={
                    "event": "http.request.completed",
                    "status_code": status_code,
                    "duration_ms": round(duration * 1000, 2),
                },
            )
        else:
            request_finished(request.method, route, status_code, duration, record=False)
        reset_request_context(token)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else None
    code = detail.get("code") if detail else f"HTTP_{exc.status_code}"
    message = detail.get("message") if detail else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content=error(request, code, message, detail.get("details") if detail else None),
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=error(request, "VALIDATION_ERROR", "请求参数校验失败", exc.errors()),
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logger.exception("Unhandled backend error", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content=error(request, "INTERNAL_ERROR", "服务器内部错误"),
    )


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/health/ready")
async def readiness():
    return {"status": "ready", "dependencies": await db.readiness()}


@app.get("/metrics", include_in_schema=False)
async def metrics(request: Request):
    if settings.METRICS_TOKEN:
        authorization = request.headers.get("Authorization", "")
        supplied = request.headers.get("X-Metrics-Token", "")
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:]
        if not hmac.compare_digest(supplied, settings.METRICS_TOKEN):
            raise HTTPException(status_code=401, detail="指标访问凭证无效")
    try:
        operational = await render_operational_metrics()
        collection_error = 0
    except Exception:
        logger.exception(
            "Operational metrics collection failed",
            extra={"event": "metrics.collection.failed"},
        )
        operational = ""
        collection_error = 1
    body = (
        render_http_metrics()
        + operational
        + "# HELP devmind_metrics_collection_error Operational metric collection failure.\n"
        + "# TYPE devmind_metrics_collection_error gauge\n"
        + f"devmind_metrics_collection_error {collection_error}\n"
    )
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=settings.BACKEND_PORT)
