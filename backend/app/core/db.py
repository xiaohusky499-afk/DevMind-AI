"""数据库与缓存连接管理。

- PostgreSQL：asyncpg 连接池，避免每次请求新建连接
- Redis：按业务拆多个 db：
  db 1 = 短信验证码
  db 2 = 用户问题缓存（解决缓存穿透/雪崩）
  db 3 = JWT 黑名单
"""
import asyncpg
from redis import asyncio as aioredis

from .config import settings


class Database:
    def __init__(self):
        self.postgres_pool: asyncpg.Pool | None = None
        self.redis_sms = None
        self.redis_question = None
        self.redis_token = None

    async def connect(self):
        """应用启动时初始化连接池。"""
        options = {
            "min_size": settings.POSTGRES_POOL_MINSIZE,
            "max_size": settings.POSTGRES_POOL_SIZE,
            "command_timeout": 30,
        }
        if settings.DATABASE_URL:
            self.postgres_pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL, **options)
        else:
            self.postgres_pool = await asyncpg.create_pool(
                host=settings.POSTGRES_HOST,
                port=settings.POSTGRES_PORT,
                user=settings.POSTGRES_USER,
                password=settings.POSTGRES_PASSWORD,
                database=settings.POSTGRES_DB,
                **options,
            )

        password = settings.REDIS_PASSWORD or None
        self.redis_sms = aioredis.from_url(
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_SMS_DB}",
            password=password,
            decode_responses=True,
        )
        self.redis_question = aioredis.from_url(
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_QUESTION_DB}",
            password=password,
            decode_responses=True,
        )
        self.redis_token = aioredis.from_url(
            f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_TOKEN_DB}",
            password=password,
            decode_responses=True,
        )

    async def close(self):
        """应用关闭时释放连接池。"""
        if self.postgres_pool:
            await self.postgres_pool.close()
        for redis_client in (self.redis_sms, self.redis_question, self.redis_token):
            if redis_client:
                await redis_client.aclose()

    async def readiness(self) -> dict[str, str]:
        """确认正式请求依赖的 PostgreSQL 与 Redis 均可用。"""
        async with self.postgres_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        await self.redis_question.ping()
        return {"postgresql": "ready", "redis": "ready"}

    async def fetch_one(self, sql: str, params: tuple = ()) -> dict | None:
        """查询单行。"""
        async with self.postgres_pool.acquire() as conn:
            row = await conn.fetchrow(sql, *params)
            return dict(row) if row else None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        """查询多行。"""
        async with self.postgres_pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [dict(row) for row in rows]

    async def execute(self, sql: str, params: tuple = ()) -> int:
        """执行写操作，返回受影响行数。"""
        async with self.postgres_pool.acquire() as conn:
            status = await conn.execute(sql, *params)
            tail = status.rsplit(" ", 1)[-1]
            return int(tail) if tail.isdigit() else 0


db = Database()
