# internal_kb_qa（企业版知识库问答核心包）

本目录为「企业内部技术知识库智能问答系统」的核心实现目录，任务分工映射：

| 任务 | 目录/文件 |
|------|-----------|
| T1 文档采集/脱敏/FAQ | `data/`、`scripts/collect_docs.py`、`scripts/build_faq.py`、`scripts/desensitize.py` |
| T2 多格式文档解析 | `document_loaders/`，统一入口 `load(file_path) -> list[Document]` |
| T3 切分与向量化 | `text_splitters/`、`core/vector_store.py`、`scripts/ingest_documents.py` |
| T4 混合检索 | `core/hybrid_search.py`，接口 `search(query, top_k, filter)` |
| T5 FAQ 精确匹配 | `postgres_qa/` 与 `core/faq_matcher.py` |
| T6 意图分类 | `core/intent_classifier.py`，接口 `classify(query)` |
| T7 重排与生成 | `core/reranker.py`、`core/rag_generator.py`、`core/prompts.py` |
| T8 问答服务 API | `api/`、根目录 `app.py` |
| T10 评估 | `rag_assesment/`（`eval_set.json`、RAGAS 脚本） |
| T12 客户端后端 | 根目录 `backend/` |
| T13 检索策略 | `core/query_rewrite.py`、`core/search_strategy.py` |
| T14 通用日志/审计 | `core/audit_logger.py` |

权限/脱敏/审计是硬约束：检索过滤、敏感信息脱敏、审计留痕在实现中不可绕过。
