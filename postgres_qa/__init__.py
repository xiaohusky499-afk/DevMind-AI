"""PostgreSQL 高频 FAQ 精确匹配模块（T5）。"""
from .cache.redis_client import RedisClient  # noqa: F401
from .db.postgres_client import PostgresClient  # noqa: F401
from .retrieval.bm25_search import BM25Search  # noqa: F401
