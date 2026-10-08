"""后端服务配置。

配置优先级：环境变量 > config.local.ini > config.ini（项目根目录）
"""

import configparser
import os
from pathlib import Path

# backend/app/core/config.py -> 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def read_secret(name: str, fallback: str = "") -> str:
    """读取环境变量或 Docker/Kubernetes 挂载的密钥文件。

    ``NAME`` 与 ``NAME_FILE`` 只能配置一个，避免部署时无法判断实际生效值。
    """
    value = os.getenv(name)
    file_name = os.getenv(f"{name}_FILE")
    if value is not None and file_name:
        raise RuntimeError(f"{name} 与 {name}_FILE 不能同时配置")
    if not file_name:
        return value if value is not None else fallback
    path = Path(file_name)
    try:
        secret = path.read_text(encoding="utf-8").rstrip("\r\n")
    except OSError as exc:
        raise RuntimeError(f"无法读取密钥文件 {name}_FILE: {path}") from exc
    if not secret:
        raise RuntimeError(f"密钥文件 {name}_FILE 不能为空")
    return secret


class Settings:
    def __init__(self):
        parser = configparser.ConfigParser()
        parser.read(
            [PROJECT_ROOT / "config.ini", PROJECT_ROOT / "config.local.ini"],
            encoding="utf-8",
        )

        # PostgreSQL 16
        self.POSTGRES_HOST = os.getenv(
            "POSTGRES_HOST", parser.get("postgresql", "host", fallback="localhost")
        )
        self.POSTGRES_PORT = int(
            os.getenv(
                "POSTGRES_PORT", parser.get("postgresql", "port", fallback="5432")
            )
        )
        self.POSTGRES_USER = os.getenv(
            "POSTGRES_USER", parser.get("postgresql", "user", fallback="devmind")
        )
        self.POSTGRES_PASSWORD = read_secret(
            "POSTGRES_PASSWORD", parser.get("postgresql", "password", fallback="")
        )
        self.POSTGRES_DB = os.getenv(
            "POSTGRES_DB",
            parser.get("postgresql", "database", fallback="internal_tech_kb"),
        )
        self.POSTGRES_POOL_SIZE = int(os.getenv("POSTGRES_POOL_SIZE", "20"))
        self.POSTGRES_POOL_MINSIZE = int(os.getenv("POSTGRES_POOL_MINSIZE", "2"))
        self.DATABASE_URL = read_secret("DATABASE_URL")

        # Redis 公共连接参数
        self.REDIS_HOST = os.getenv(
            "REDIS_HOST", parser.get("redis", "host", fallback="localhost")
        )
        self.REDIS_PORT = int(
            os.getenv("REDIS_PORT", parser.get("redis", "port", fallback="6379"))
        )
        self.REDIS_PASSWORD = read_secret(
            "REDIS_PASSWORD", parser.get("redis", "password", fallback="")
        )
        self.REDIS_DB = int(
            os.getenv("REDIS_DB", parser.get("redis", "db", fallback="0"))
        )

        # Redis 业务库隔离：
        # 1 = 短信验证码；2 = 用户问题缓存；3 = JWT 黑名单
        self.REDIS_SMS_DB = int(os.getenv("REDIS_SMS_DB", "1"))
        self.REDIS_QUESTION_DB = int(os.getenv("REDIS_QUESTION_DB", "2"))
        self.REDIS_TOKEN_DB = int(os.getenv("REDIS_TOKEN_DB", "3"))

        # 应用 / 安全
        self.APP_ENV = os.getenv("APP_ENV", "development").lower()
        self.BACKEND_PORT = int(os.getenv("BACKEND_PORT", "15200"))
        self.CORS_ORIGINS = [
            origin.strip()
            for origin in os.getenv(
                "CORS_ORIGINS",
                "http://127.0.0.1:5173,http://localhost:5173",
            ).split(",")
            if origin.strip()
        ]
        self.JWT_SECRET = read_secret(
            "JWT_SECRET", "dev-secret-change-me-at-least-32-bytes"
        )
        self.JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
        self.JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "1"))
        self.SMS_CODE_TTL = int(os.getenv("SMS_CODE_TTL", "300"))
        self.SMS_SEND_INTERVAL = int(os.getenv("SMS_SEND_INTERVAL", "60"))
        self.SMS_SEND_DAILY_LIMIT = max(1, int(os.getenv("SMS_SEND_DAILY_LIMIT", "10")))
        self.SMS_SEND_IP_HOURLY_LIMIT = max(
            1, int(os.getenv("SMS_SEND_IP_HOURLY_LIMIT", "30"))
        )
        self.SMS_VERIFY_LIMIT = max(1, int(os.getenv("SMS_VERIFY_LIMIT", "8")))
        self.REGISTER_IP_HOURLY_LIMIT = max(
            1, int(os.getenv("REGISTER_IP_HOURLY_LIMIT", "5"))
        )
        self.LOGIN_IP_LIMIT = max(1, int(os.getenv("LOGIN_IP_LIMIT", "30")))
        self.LOGIN_ACCOUNT_LIMIT = max(
            1, int(os.getenv("LOGIN_ACCOUNT_LIMIT", "10"))
        )
        self.LOGIN_RATE_WINDOW_SECONDS = max(
            60, int(os.getenv("LOGIN_RATE_WINDOW_SECONDS", "900"))
        )
        self.LOGIN_LOCK_THRESHOLD = max(
            2, int(os.getenv("LOGIN_LOCK_THRESHOLD", "5"))
        )
        self.LOGIN_LOCK_SECONDS = max(60, int(os.getenv("LOGIN_LOCK_SECONDS", "900")))
        self.JWT_REQUEST_LIMIT = max(1, int(os.getenv("JWT_REQUEST_LIMIT", "300")))
        self.JWT_RATE_WINDOW_SECONDS = max(
            10, int(os.getenv("JWT_RATE_WINDOW_SECONDS", "60"))
        )
        self.APP_DEBUG = os.getenv("APP_DEBUG", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
        self.METRICS_TOKEN = read_secret("METRICS_TOKEN")

        # 问题缓存：随机 TTL 防止缓存雪崩；空值短 TTL 防止穿透
        self.QUESTION_CACHE_TTL = int(os.getenv("QUESTION_CACHE_TTL", "600"))
        self.QUESTION_EMPTY_TTL = int(os.getenv("QUESTION_EMPTY_TTL", "60"))
        self.CACHE_LOCK_TIMEOUT = int(os.getenv("CACHE_LOCK_TIMEOUT", "5"))

        # 文档对象存储与索引
        self.MINIO_ENDPOINT = os.getenv(
            "MINIO_ENDPOINT",
            parser.get("storage", "endpoint", fallback="127.0.0.1:9000"),
        )
        self.MINIO_ACCESS_KEY = read_secret(
            "MINIO_ACCESS_KEY",
            parser.get("storage", "access_key", fallback="minioadmin"),
        )
        self.MINIO_SECRET_KEY = read_secret(
            "MINIO_SECRET_KEY",
            parser.get("storage", "secret_key", fallback="minioadmin"),
        )
        self.MINIO_BUCKET = os.getenv(
            "MINIO_BUCKET",
            parser.get("storage", "bucket", fallback="devmind-documents"),
        )
        self.MINIO_SECURE = os.getenv(
            "MINIO_SECURE", parser.get("storage", "secure", fallback="false")
        ).lower() in {"1", "true", "yes", "on"}
        self.DOCUMENT_MAX_SIZE = int(
            os.getenv("DOCUMENT_MAX_SIZE", str(50 * 1024 * 1024))
        )
        self.DOCUMENT_ARCHIVE_MAX_FILES = max(
            10, int(os.getenv("DOCUMENT_ARCHIVE_MAX_FILES", "5000"))
        )
        self.DOCUMENT_ARCHIVE_MAX_UNCOMPRESSED = max(
            self.DOCUMENT_MAX_SIZE,
            int(os.getenv("DOCUMENT_ARCHIVE_MAX_UNCOMPRESSED", str(200 * 1024 * 1024))),
        )
        self.DOCUMENT_ARCHIVE_MAX_RATIO = max(
            10, int(os.getenv("DOCUMENT_ARCHIVE_MAX_RATIO", "100"))
        )
        self.CLAMAV_ENABLED = os.getenv(
            "CLAMAV_ENABLED", "false"
        ).lower() in {"1", "true", "yes", "on"}
        self.CLAMAV_HOST = os.getenv("CLAMAV_HOST", "127.0.0.1")
        self.CLAMAV_PORT = int(os.getenv("CLAMAV_PORT", "3310"))
        self.CLAMAV_TIMEOUT_SECONDS = max(
            1.0, float(os.getenv("CLAMAV_TIMEOUT_SECONDS", "30"))
        )
        self.DOCUMENT_CHUNK_SIZE = int(
            os.getenv(
                "DOCUMENT_CHUNK_SIZE",
                parser.get("retrieval", "chunk_size", fallback="1200"),
            )
        )
        self.DOCUMENT_CHUNK_OVERLAP = int(
            os.getenv(
                "DOCUMENT_CHUNK_OVERLAP",
                parser.get("retrieval", "chunk_overlap", fallback="150"),
            )
        )
        self.MILVUS_HOST = os.getenv(
            "MILVUS_HOST", parser.get("milvus", "host", fallback="127.0.0.1")
        )
        self.MILVUS_PORT = int(
            os.getenv("MILVUS_PORT", parser.get("milvus", "port", fallback="19530"))
        )
        self.MILVUS_COLLECTION = os.getenv(
            "MILVUS_COLLECTION",
            parser.get("milvus", "collection_name", fallback="internal_tech_kb"),
        )
        self.MILVUS_DATABASE = os.getenv(
            "MILVUS_DATABASE",
            parser.get("milvus", "database_name", fallback="internal_tech_kb"),
        )
        self.MILVUS_INDEXING_ENABLED = os.getenv(
            "MILVUS_INDEXING_ENABLED", "true"
        ).lower() in {"1", "true", "yes", "on"}
        self.BGE_M3_MODEL_PATH = os.getenv(
            "BGE_M3_MODEL_PATH",
            str(PROJECT_ROOT / "internal_kb_qa" / "models" / "bge-m3"),
        )
        self.DOCUMENT_WORKER_POLL_SECONDS = max(
            0.1, float(os.getenv("DOCUMENT_WORKER_POLL_SECONDS", "1"))
        )
        self.DOCUMENT_WORKER_HEARTBEAT_SECONDS = max(
            1.0, float(os.getenv("DOCUMENT_WORKER_HEARTBEAT_SECONDS", "10"))
        )
        self.DOCUMENT_TASK_LEASE_SECONDS = max(
            10, int(os.getenv("DOCUMENT_TASK_LEASE_SECONDS", "60"))
        )
        self.DOCUMENT_TASK_TIMEOUT_SECONDS = max(
            30, int(os.getenv("DOCUMENT_TASK_TIMEOUT_SECONDS", "1800"))
        )
        self.DOCUMENT_TASK_MAX_ATTEMPTS = max(
            1, int(os.getenv("DOCUMENT_TASK_MAX_ATTEMPTS", "3"))
        )
        self.DOCUMENT_TASK_RETRY_BASE_SECONDS = max(
            1, int(os.getenv("DOCUMENT_TASK_RETRY_BASE_SECONDS", "10"))
        )
        self.BGE_DEVICE = os.getenv("BGE_DEVICE", "cpu")
        self.BGE_M3_DIM = int(
            os.getenv("BGE_M3_DIM", parser.get("embedding", "dim", fallback="1024"))
        )
        self.LLM_MODEL = os.getenv(
            "LLM_MODEL", parser.get("llm", "model", fallback="qwen-plus")
        )
        self.DASHSCOPE_API_KEY = read_secret(
            "DASHSCOPE_API_KEY", parser.get("llm", "dashscope_api_key", fallback="")
        )
        self.DASHSCOPE_BASE_URL = os.getenv(
            "DASHSCOPE_BASE_URL",
            parser.get(
                "llm",
                "dashscope_base_url",
                fallback="https://dashscope.aliyuncs.com/compatible-mode/v1",
            ),
        )
        self.RETRIEVAL_TOP_K = int(
            os.getenv(
                "RETRIEVAL_TOP_K", parser.get("retrieval", "top_k", fallback="20")
            )
        )
        self.RERANK_TOP_K = int(
            os.getenv(
                "RERANK_TOP_K", parser.get("retrieval", "rerank_top_k", fallback="5")
            )
        )
        self.RAG_LOCAL_MODEL_CONCURRENCY = max(
            1, int(os.getenv("RAG_LOCAL_MODEL_CONCURRENCY", "1"))
        )
        self.RAG_LLM_CONCURRENCY = max(
            1, int(os.getenv("RAG_LLM_CONCURRENCY", "8"))
        )
        self.RAG_MODEL_TIMEOUT_SECONDS = max(
            1.0, float(os.getenv("RAG_MODEL_TIMEOUT_SECONDS", "120"))
        )
        self.RAG_LLM_TIMEOUT_SECONDS = max(
            1.0, float(os.getenv("RAG_LLM_TIMEOUT_SECONDS", "120"))
        )

        if self.JWT_ALGORITHM not in {"HS256", "HS384", "HS512"}:
            raise RuntimeError("JWT_ALGORITHM 只允许 HS256/HS384/HS512")
        if self.APP_ENV != "development":
            if (
                self.JWT_SECRET == "dev-secret-change-me-at-least-32-bytes"
                or len(self.JWT_SECRET.encode("utf-8")) < 32
            ):
                raise RuntimeError("生产环境必须配置至少 32 字节的独立 JWT_SECRET")
            if self.APP_DEBUG:
                raise RuntimeError("生产环境必须关闭 APP_DEBUG")
            if not self.CLAMAV_ENABLED:
                raise RuntimeError("生产环境必须启用 CLAMAV_ENABLED 文件病毒扫描")
            if not self.DATABASE_URL and (
                not self.POSTGRES_PASSWORD or self.POSTGRES_PASSWORD == "devmind123"
            ):
                raise RuntimeError("生产环境必须配置独立的 PostgreSQL 密码或 DATABASE_URL")
            if not self.REDIS_PASSWORD:
                raise RuntimeError("生产环境必须配置独立的 REDIS_PASSWORD")
            if (
                self.MINIO_ACCESS_KEY == "minioadmin"
                or self.MINIO_SECRET_KEY == "minioadmin"
                or len(self.MINIO_SECRET_KEY) < 16
            ):
                raise RuntimeError("生产环境必须配置独立的 MinIO 访问密钥")
            if len(self.METRICS_TOKEN) < 24:
                raise RuntimeError("生产环境必须配置至少 24 字节的 METRICS_TOKEN")


settings = Settings()
