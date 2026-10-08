"""T5 高频技术 FAQ 精确匹配。

对外接口：
    faq_match(query: str) -> FAQHit | None
命中即秒回，低于阈值返回 None 走 RAG 主链路。
"""
# TODO(T5 算法组): 复用 postgres_qa 的 PostgreSQL + BM25 检索能力。
