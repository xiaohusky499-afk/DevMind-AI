"""BGE-M3 向量化与 Milvus 写入。"""

from __future__ import annotations

import time
from pathlib import Path
from threading import Lock

from milvus_model.hybrid import BGEM3EmbeddingFunction
from pymilvus import AnnSearchRequest, DataType, MilvusClient, WeightedRanker

from ..core.config import settings

_client: MilvusClient | None = None
_embedder: BGEM3EmbeddingFunction | None = None
_embedding_lock = Lock()


def _milvus() -> MilvusClient:
    global _client
    if _client is None:
        uri = f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"
        root = MilvusClient(uri=uri)
        database = settings.MILVUS_DATABASE
        if database not in root.list_databases():
            root.create_database(database)
        _client = MilvusClient(uri=uri, db_name=database)
        _ensure_collection(_client)
    return _client


def _embedding() -> BGEM3EmbeddingFunction:
    global _embedder
    if _embedder is None:
        model_path = Path(settings.BGE_M3_MODEL_PATH)
        if not (model_path / "config.json").exists():
            raise RuntimeError(
                "BGE-M3 模型未就绪，请运行: uv run python -m "
                "internal_kb_qa.scripts.download_models bge-m3"
            )
        _embedder = BGEM3EmbeddingFunction(
            model_name_or_path=str(model_path),
            use_fp16=settings.BGE_DEVICE == "cuda",
            device=settings.BGE_DEVICE,
        )
    return _embedder


def _ensure_collection(client: MilvusClient) -> None:
    name = settings.MILVUS_COLLECTION
    if client.has_collection(name):
        return
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("id", DataType.VARCHAR, is_primary=True, max_length=36)
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=settings.BGE_M3_DIM)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
    schema.add_field("text", DataType.VARCHAR, max_length=65535)
    schema.add_field("document_id", DataType.VARCHAR, max_length=36)
    schema.add_field("knowledge_base_id", DataType.VARCHAR, max_length=36)
    schema.add_field("source", DataType.VARCHAR, max_length=500)
    schema.add_field("location_label", DataType.VARCHAR, max_length=160)
    schema.add_field("parent_id", DataType.VARCHAR, max_length=36)
    schema.add_field("parent_content", DataType.VARCHAR, max_length=65535)
    schema.add_field("timestamp", DataType.VARCHAR, max_length=64)
    index = client.prepare_index_params()
    index.add_index("dense_vector", index_type="AUTOINDEX", metric_type="IP")
    index.add_index(
        "sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP"
    )
    client.create_collection(name, schema=schema, index_params=index)


def _sparse_rows(matrix) -> list[dict[int, float]]:
    rows = []
    for index in range(matrix.shape[0]):
        row = matrix[index]
        if hasattr(row, "col"):
            rows.append({int(i): float(v) for i, v in zip(row.col, row.data)})
        else:
            row = row.tocsr()
            rows.append({int(i): float(v) for i, v in zip(row.indices, row.data)})
    return rows


def index_chunks(chunks: list[dict]) -> int:
    if not chunks:
        return 0
    with _embedding_lock:
        embeddings = _embedding()([chunk["content"] for chunk in chunks])
    dense = embeddings["dense"]
    sparse = _sparse_rows(embeddings["sparse"])
    rows = [
        {
            "id": chunk["id"],
            "dense_vector": dense[index].tolist(),
            "sparse_vector": sparse[index],
            "text": chunk["content"],
            "document_id": chunk["document_id"],
            "knowledge_base_id": chunk["knowledge_base_id"],
            "source": chunk["source"][:500],
            "location_label": chunk["location_label"][:160],
            "parent_id": chunk["document_id"],
            "parent_content": chunk["content"],
            "timestamp": chunk.get("timestamp", ""),
        }
        for index, chunk in enumerate(chunks)
    ]
    _milvus().insert(settings.MILVUS_COLLECTION, rows)
    return len(rows)


def delete_document(document_id: str) -> None:
    _milvus().delete(
        settings.MILVUS_COLLECTION, filter=f'document_id == "{document_id}"'
    )


def hybrid_search(
    query: str,
    knowledge_base_ids: list[str],
    limit: int,
    timings: dict[str, int] | None = None,
    document_ids: list[str] | None = None,
) -> list[dict]:
    if not knowledge_base_ids or document_ids == []:
        return []
    started = time.perf_counter()
    with _embedding_lock:
        embeddings = _embedding()([query])
    if timings is not None:
        timings["embedding_ms"] = round((time.perf_counter() - started) * 1000)
    dense = embeddings["dense"][0].tolist()
    sparse = _sparse_rows(embeddings["sparse"])[0]
    allowed = ", ".join(f'"{item}"' for item in knowledge_base_ids)
    expr = f"knowledge_base_id in [{allowed}]"
    if document_ids is not None:
        allowed_documents = ", ".join(f'"{item}"' for item in document_ids)
        expr += f" and document_id in [{allowed_documents}]"
    requests = [
        AnnSearchRequest(
            [dense],
            "dense_vector",
            {"metric_type": "IP", "params": {}},
            limit=limit,
            expr=expr,
        ),
        AnnSearchRequest(
            [sparse],
            "sparse_vector",
            {"metric_type": "IP", "params": {}},
            limit=limit,
            expr=expr,
        ),
    ]
    started = time.perf_counter()
    results = _milvus().hybrid_search(
        settings.MILVUS_COLLECTION,
        requests,
        WeightedRanker(0.7, 0.3),
        limit,
        output_fields=[
            "text",
            "document_id",
            "knowledge_base_id",
            "source",
            "location_label",
        ],
    )[0]
    if timings is not None:
        timings["milvus_ms"] = round((time.perf_counter() - started) * 1000)
    return [
        {
            "chunk_id": str(hit["id"]),
            "score": max(0.0, min(1.0, float(hit["distance"]))),
            **dict(hit["entity"]),
        }
        for hit in results
    ]
