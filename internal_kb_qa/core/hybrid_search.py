"""T4 BM25 + Dense 混合检索（RRF 融合）。

对外接口：
    search(query: str, top_k: int, filter: dict = None) -> list[Hit]
"""
# TODO(T4 算法组): Milvus hybrid search；filter 必须注入 team/system/security_level。

# -*- coding: utf-8 -*-
"""
混合检索模块（Hybrid Retrieval with Rerank）
功能：基于 BGE-M3 的稠密+稀疏混合检索 + BGE-Reranker 重排序
"""

import os
import time
import torch.cuda
from langchain_core.documents import Document
from sentence_transformers import CrossEncoder
from pymilvus import MilvusClient, AnnSearchRequest, WeightedRanker
from milvus_model.hybrid import BGEM3EmbeddingFunction

from base.config import Config
from base.logger import logger
from internal_kb_qa.core.hit import Hit

conf = Config()


class HybridRetriever:
    """混合检索器"""

    def __init__(self):
        # 获取当前文件所在目录的绝对路径
        current_dir = os.path.dirname(os.path.abspath(__file__))
        # 获取core文件所在的目录的绝对路径
        self.rag_qa_path = os.path.dirname(current_dir)

        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        logger.info(f"设备: {self.device}")

        # 初始化 BGE-Reranker 模型，用于重排序检索结果
        reranker_path = os.path.join(self.rag_qa_path, 'models', 'bge-reranker-v2-m3')
        logger.info(f"加载 Reranker: {reranker_path}")
        start = time.time()
        self.reranker = CrossEncoder(reranker_path, device=self.device)
        logger.info(f"Reranker 加载完成，耗时: {time.time() - start:.2f}s")

        # 初始化 BGE-M3 嵌入函数
        m3_path = os.path.join(self.rag_qa_path, 'models', 'bge-m3')
        logger.info(f"加载 BGE-M3: {m3_path}")
        start = time.time()
        self.embedding_function = BGEM3EmbeddingFunction(
            model_name_or_path=m3_path,
            use_fp16=(self.device == 'cuda'),
            device=self.device
        )
        logger.info(f"BGE-M3 加载完成，耗时: {time.time() - start:.2f}s")

        # 设置 Milvus 主机地址
        host = conf.MILVUS_HOST
        # 设置 Milvus 端口号
        port = conf.MILVUS_PORT
        # 设置 Milvus 数据库名称
        database = conf.MILVUS_DATABASE_NAME

        # 初始化 Milvus 客户端，连接到指定主机和数据库
        logger.info(f"连接 Milvus: http://{host}:{port}/{database}")
        self.client = MilvusClient(uri=f"http://{host}:{port}", db_name=database)
        logger.info("Milvus 连接成功")

    def doc_from_hit(self, hit):
        # 创建并返回 Document 对象，填充内容和元数据
        return Document(
            page_content=hit.get("text"),
            metadata={
                "parent_id": hit.get("parent_id"),
                "parent_content": hit.get("parent_content"),
                "source": hit.get("source"),
                "timestamp": hit.get("timestamp")
            }
        )

    def get_unique_parent_docs(self, sub_chunks):
        # 初始化集合，用于存储已处理的父块内容（去重）
        parent_contents = set()
        # 初始化列表，用于存储唯一父文档
        unique_docs = []
        # 遍历所有子块
        for chunk in sub_chunks:
            # 获取子块的父块内容，默认为子块内容
            parent_content = chunk.metadata.get("parent_content", chunk.page_content)
            # 检查父块内容是否非空且未重复
            if parent_content and parent_content not in parent_contents:
                # 创建新的 Document 对象，包含父块内容和元数据
                unique_docs.append(Document(page_content=parent_content, metadata=chunk.metadata))
                # 将父块内容添加到去重集合
                parent_contents.add(parent_content)
            # 返回去重后的父文档列表
        return unique_docs

    def search(self, query, k=conf.TOP_K, source_filter=None):
        total_start = time.time()
        logger.info(f"查询: {query[:80]}{'...' if len(query) > 80 else ''}")
        logger.info(f"参数: k={k}, source_filter={source_filter}")

        # 使用 BGE-M3 嵌入函数生成查询的嵌入
        embed_start = time.time()
        query_embeddings = self.embedding_function([query])
        # 获取查询的稠密向量
        dense_query_vector = query_embeddings["dense"][0]
        logger.info(f"向量生成耗时: {time.time() - embed_start:.3f}s")

        # 初始化查询的稀疏向量字典
        sparse_query_vector = {}
        try:
            # 新版本 milvus-model 使用 coo_array 格式
            row = query_embeddings["sparse"][0]
            if hasattr(row, 'col'):  # coo_array 格式
                indices = row.col
                values = row.data
            else:  # csr_matrix 格式
                indices = row.indices
                values = row.data
        except Exception as e:
            # 兼容旧版本 milvus-model
            row = query_embeddings["sparse"].getrow(0)
            indices = row.indices
            values = row.data

        # 将索引和值配对，填充稀疏向量字典
        for idx, value in zip(indices, values):
            sparse_query_vector[idx] = value

        # 初始化过滤表达式，默认不过滤
        filter_expr = f"source == '{source_filter}'" if source_filter else ""

        # 创建稠密向量搜索请求
        dense_request = AnnSearchRequest(
            data=[dense_query_vector],
            anns_field="dense_vector",
            param={"metric_type": "IP", "params": {"nprobe": 10}},
            limit=k,
            expr=filter_expr
        )
        # 创建稀疏向量搜索请求
        sparse_request = AnnSearchRequest(
            data=[sparse_query_vector],
            anns_field="sparse_vector",
            param={"metric_type": "IP", "params": {}},
            limit=k,
            expr=filter_expr
        )

        # 创建加权排序器，稀疏向量权重 0.3，稠密向量权重 0.7
        ranker = WeightedRanker(0.7, 0.3)

        # 执行混合搜索，返回 Top-K 结果
        search_start = time.time()
        results = self.client.hybrid_search(
            collection_name=conf.MILVUS_COLLECTION_NAME,
            reqs=[dense_request, sparse_request],
            ranker=ranker,
            limit=k,
            output_fields=["text", "parent_id", "parent_content", "source", "timestamp"]
        )[0]
        logger.info(f"检索耗时: {time.time() - search_start:.3f}s, 返回 {len(results)} 条")

        # 将上述搜索到的结果进行Document对象封装，便于查询使用
        sub_chunks = [self.doc_from_hit(hit["entity"]) for hit in results]

        # 从子块中提取去重的父文档
        parent_docs = self.get_unique_parent_docs(sub_chunks)
        logger.info(f"去重后父文档数: {len(parent_docs)}")

        # 如果只有1个文档或者没有，直接返回跳过重排序
        if len(parent_docs) < 2:
            logger.info("父文档少于2个，跳过重排序")
            return parent_docs[:conf.CANDIDATE_M]

        # 如果有父文档，进行重排序
        if parent_docs:
            # 创建查询与文档内容的配对列表
            pairs = [[query, doc.page_content] for doc in parent_docs]
            # 使用 BGE-Reranker 计算每个配对的得分
            rerank_start = time.time()
            scores = self.reranker.predict(pairs)
            logger.info(f"重排序耗时: {time.time() - rerank_start:.3f}s")
            # 根据得分从高到低排序文档
            ranked_parent_docs = [doc for _, doc in sorted(zip(scores, parent_docs), reverse=True)]
        else:
            ranked_parent_docs = []

        # 返回前 m 个重排序后的文档
        final_docs = ranked_parent_docs[:conf.RERANK_TOP_K]
        logger.info(f"最终返回 {len(final_docs)} 个文档, 总耗时: {time.time() - total_start:.3f}s")
        return final_docs


# T4 对外契约：模块级 search 函数
_retriever: HybridRetriever | None = None


def search(query: str, top_k: int, filter: dict | None = None) -> list[Hit]:
    """BM25 + Dense 混合检索（T4 对外接口）。

    Args:
        query: 用户问题。
        top_k: 召回条数。
        filter: 元数据过滤条件，支持 team / system / security_level。

    Returns:
        list[Hit]: 命中的文档片段。
    """
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()

    # 当前 HybridRetriever 仅支持 source 过滤，先做兼容映射
    filter = filter or {}
    source_filter = filter.get("source") or filter.get("system") or filter.get("team")

    docs = _retriever.search(query, k=top_k, source_filter=source_filter)
    return [
        Hit(
            text=doc.page_content,
            score=float(doc.metadata.get("score", 0.5)),
            metadata=doc.metadata,
        )
        for doc in docs
    ]


if __name__ == "__main__":
    retriever = HybridRetriever()
    query = "AI学科学费是多少？"
    results = retriever.search(query, source_filter='ai')
    print(f'results-->{results}')
    print(f'results-->{len(results)}')
