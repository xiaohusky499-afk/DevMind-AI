# rag_qa

本目录保留早期 RAG 组件与数据结构，部分实现仍为占位代码：

- `edu_document_loaders/`：T2 复用的文档解析 loader
- `edu_text_spliter/`：T3 切分逻辑
- `core/vector_store.py`：T3/T4 Milvus 向量库参考
- `core/bert_query_classifier/`：T6 意图分类器参考
- `models/`：BGE-M3、bge-reranker-v2-m3、文档语义分段模型
- `rag_assesment/`：T10 RAGAS 评估流程

> 新企业版代码请优先实现到 `internal_kb_qa/`；模型权重等大文件不提交 Git。
