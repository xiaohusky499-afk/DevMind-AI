"""T3/T4 Milvus 向量库。

对外接口（T3）：
    insert(chunks: list[Chunk]) -> int
参考：rag_qa/core/vector_store.py
"""
# TODO(T3 算法组): BGE-M3 Embedding -> collection internal_tech_kb。
# TODO(T4 算法组): 权限/系统元数据过滤、多租户 partition 隔离。
class VectorStore:
    def insert(self, chunks):
        raise NotImplementedError("TODO(T3/T4): implement Milvus insert/search")
