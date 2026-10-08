"""配置加载模块。

"""
import ast
import configparser
import os


class Config:
    """加载 config.ini 并暴露 PostgreSQL/Redis/Milvus/LLM/安全/检索配置。"""

    def __init__(self, config_file: str = "config.ini"):
        self.config = configparser.ConfigParser()
        self.config.read(config_file, encoding="utf-8")

        self.POSTGRES_HOST = self.config.get("postgresql", "host", fallback="localhost")
        self.POSTGRES_PORT = self.config.getint("postgresql", "port", fallback=5432)
        self.POSTGRES_USER = self.config.get("postgresql", "user", fallback="devmind")
        self.POSTGRES_PASSWORD = self.config.get("postgresql", "password", fallback="")
        self.POSTGRES_DB = self.config.get("postgresql", "database", fallback="internal_tech_kb")

        self.REDIS_HOST = self.config.get("redis", "host", fallback="localhost")
        self.REDIS_PORT = self.config.getint("redis", "port", fallback=16379)
        self.REDIS_PASSWORD = self.config.get("redis", "password", fallback="")
        self.REDIS_DB = self.config.getint("redis", "db", fallback=0)

        self.MILVUS_HOST = self.config.get("milvus", "host", fallback="localhost")
        self.MILVUS_PORT = self.config.get("milvus", "port", fallback="19530")
        self.MILVUS_DATABASE_NAME = self.config.get("milvus", "database_name", fallback="internal_tech_kb")
        self.MILVUS_COLLECTION_NAME = self.config.get("milvus", "collection_name", fallback="internal_tech_kb")

        self.LLM_MODEL = self.config.get("llm", "model", fallback="qwen-plus")
        self.DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY") or self.config.get("llm", "dashscope_api_key", fallback="")
        self.DASHSCOPE_BASE_URL = self.config.get("llm", "dashscope_base_url", fallback="https://dashscope.aliyuncs.com/compatible-mode/v1")

        self.BGE_MODEL = self.config.get("embedding", "model_name", fallback="bge-m3")
        self.BGE_DIM = self.config.getint("embedding", "dim", fallback=1024)
        self.RERANKER_MODEL = self.config.get("reranker", "model_name", fallback="bge-reranker-v2-m3")

        self.CHUNK_SIZE = self.config.getint("retrieval", "chunk_size", fallback=1200)
        self.CHUNK_OVERLAP = self.config.getint("retrieval", "chunk_overlap", fallback=150)
        self.TOP_K = self.config.getint("retrieval", "top_k", fallback=5)
        self.RERANK_TOP_K = self.config.getint("retrieval", "rerank_top_k", fallback=3)
        self.CONFIDENCE_THRESHOLD = self.config.getfloat("retrieval", "confidence_threshold", fallback=0.75)

        self.APP_PORT = self.config.getint("app", "port", fallback=8003)
        self.VALID_SOURCES = ast.literal_eval(
            self.config.get("app", "valid_sources", fallback='["infra", "backend", "frontend", "data", "ops"]')
        )
        self.LOG_FILE = self.config.get("logger", "log_file", fallback="logs/app.log")
