"""PostgreSQL FAQ 数据访问占位模块（T5）。

正式接口已统一复用 backend.app.core.db 的 asyncpg 连接池；实现离线 FAQ
构建工具时再在此增加同步或批处理客户端。
"""


class PostgresClient:
    def __init__(self):
        raise NotImplementedError("TODO(T5): reuse the asyncpg repository or add a batch client")
