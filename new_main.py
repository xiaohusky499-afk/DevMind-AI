"""集成编排入口（T8）。

主链路：
意图分类(T6) -> FAQ 精确匹配(T5) -> 检索(T4/T13) -> 重排生成(T7) -> 转人工兜底。
"""
# TODO(T8 服务组): 初始化 postgres_qa、rag_qa 与 internal_kb_qa 各组件，
# 串联 T5/T6/T7/T13，并保留 Redis 会话缓存与 LLM 故障降级 FAQ 直回能力。
