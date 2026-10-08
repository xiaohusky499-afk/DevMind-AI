"""文档上传、解析、分块和索引服务。"""

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from asyncpg import UniqueViolationError
from fastapi import HTTPException, UploadFile

from internal_kb_qa.document_loaders import load
from internal_kb_qa.text_splitters.recursive_splitter import RecursiveSplitter

from ..core.access_control import document_access_sql
from ..core.config import settings
from ..core.db import db
from . import file_security_service, object_storage_service, vector_index_service
from .knowledge_base_service import require_role

_SOURCE_TYPES = {
    ".pdf": "pdf",
    ".md": "md",
    ".markdown": "md",
    ".docx": "docx",
    ".html": "wiki",
    ".htm": "wiki",
    ".json": "api",
}


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def _accessible_document(conn, document_id: UUID, current_user: dict):
    return await conn.fetchrow(
        f"""SELECT d.* FROM documents d
            JOIN knowledge_bases kb ON kb.id=d.knowledge_base_id
            WHERE d.id=$1 AND d.deleted_at IS NULL AND kb.deleted_at IS NULL
              AND kb.organization_id=$2
              AND {document_access_sql("d", "$3", "$4")}""",
        document_id,
        current_user["organization_id"],
        current_user.get("security_level", "public"),
        current_user.get("team"),
    )


async def _audit(
    conn, current_user: dict, action: str, document_id: UUID, request_id: str
) -> None:
    await conn.execute(
        """INSERT INTO audit_logs
           (organization_id, user_id, action, resource_type, resource_id, request_id)
           VALUES ($1, $2, $3, 'document', $4, $5)""",
        current_user["organization_id"],
        current_user["id"],
        action,
        str(document_id),
        request_id,
    )


async def _write_upload(upload: UploadFile, path: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with path.open("wb") as target:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > settings.DOCUMENT_MAX_SIZE:
                raise _error(413, "FILE_TOO_LARGE", "文件大小不能超过 50 MB")
            digest.update(chunk)
            target.write(chunk)
    if size == 0:
        raise _error(400, "EMPTY_FILE", "文件内容不能为空")
    return size, digest.hexdigest()


async def upload_document(
    upload: UploadFile,
    knowledge_base_id: UUID,
    display_name: str | None,
    metadata: dict,
    current_user: dict,
    request_id: str,
) -> dict:
    filename = Path(upload.filename or "").name
    suffix = Path(filename).suffix.casefold()
    if suffix not in _SOURCE_TYPES:
        raise _error(
            415,
            "UNSUPPORTED_FILE_TYPE",
            f"仅支持: {', '.join(sorted(_SOURCE_TYPES))}",
        )
    document_id, task_id = uuid4(), uuid4()
    object_key = (
        f"{current_user['organization_id']}/{knowledge_base_id}/{document_id}{suffix}"
    )

    async with db.postgres_pool.acquire() as conn:
        await require_role(conn, knowledge_base_id, current_user, "editor")

    with tempfile.TemporaryDirectory() as temp_dir:
        local_path = Path(temp_dir) / f"upload{suffix}"
        size, checksum = await _write_upload(upload, local_path)
        security = await file_security_service.validate_and_scan(
            local_path, suffix, upload.content_type
        )
        metadata = {
            **metadata,
            "_security": {"validated": True, "scanner": security["scanner"]},
        }
        duplicate = await db.fetch_one(
            """SELECT id FROM documents
               WHERE knowledge_base_id=$1 AND checksum=$2 AND deleted_at IS NULL""",
            (knowledge_base_id, checksum),
        )
        if duplicate:
            raise _error(409, "DUPLICATE_DOCUMENT", "相同文件已存在于该知识库")
        await asyncio.to_thread(
            object_storage_service.upload,
            str(local_path),
            object_key,
            security["mime_type"],
        )

    try:
        async with db.postgres_pool.acquire() as conn, conn.transaction():
            await require_role(conn, knowledge_base_id, current_user, "editor")
            await conn.execute(
                """INSERT INTO documents
                   (id, knowledge_base_id, name, doc_source, source_type, doc_type,
                    storage_key, mime_type, size_bytes, status, checksum, metadata, created_by)
                   VALUES ($1,$2,$3,$4,$5,$5,$6,$7,$8,'uploaded',$9,$10::jsonb,$11)""",
                document_id,
                knowledge_base_id,
                (display_name or filename).strip(),
                filename,
                _SOURCE_TYPES[suffix],
                object_key,
                security["mime_type"],
                size,
                checksum,
                json.dumps(metadata),
                current_user["id"],
            )
            await conn.execute(
                """INSERT INTO document_tasks
                   (id, document_id, task_type, max_attempts, timeout_seconds)
                   VALUES ($1, $2, 'ingest', $3, $4)""",
                task_id,
                document_id,
                settings.DOCUMENT_TASK_MAX_ATTEMPTS,
                settings.DOCUMENT_TASK_TIMEOUT_SECONDS,
            )
            await _audit(
                conn, current_user, "document.uploaded", document_id, request_id
            )
    except UniqueViolationError as exc:
        await asyncio.to_thread(object_storage_service.delete, object_key)
        raise _error(409, "DUPLICATE_DOCUMENT", "相同文件已存在于该知识库") from exc
    except Exception:
        await asyncio.to_thread(object_storage_service.delete, object_key)
        raise

    return {"id": document_id, "status": "uploaded", "task_id": task_id}


async def _set_progress(
    document_id: UUID,
    task_id: UUID,
    document_status: str,
    progress: int,
) -> None:
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE documents SET status=$2 WHERE id=$1", document_id, document_status
        )
        await conn.execute(
            """UPDATE document_tasks SET status='running', progress=$2,
                      started_at=COALESCE(started_at, now()) WHERE id=$1""",
            task_id,
            progress,
        )


async def process_document(document_id: UUID, task_id: UUID) -> None:
    document = await db.fetch_one(
        """SELECT d.id, d.knowledge_base_id, d.name, d.doc_source, d.storage_key
           FROM documents d WHERE d.id=$1 AND d.deleted_at IS NULL""",
        (document_id,),
    )
    if not document:
        raise RuntimeError("文档不存在或已删除")
    suffix = Path(document["doc_source"]).suffix.casefold()
    await _set_progress(document_id, task_id, "parsing", 15)
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / f"document{suffix}"
        await asyncio.to_thread(
            object_storage_service.download, document["storage_key"], str(path)
        )
        source_documents = await asyncio.to_thread(load, str(path))
        if not source_documents:
            raise RuntimeError("文档没有可索引的文本内容")
        await _set_progress(document_id, task_id, "chunking", 40)
        splitter = RecursiveSplitter(
            settings.DOCUMENT_CHUNK_SIZE, settings.DOCUMENT_CHUNK_OVERLAP
        )
        split_documents = await asyncio.to_thread(splitter.split, source_documents)

    chunks = []
    for number, chunk in enumerate(split_documents):
        chunk_id = uuid4()
        page = chunk.metadata.get("page")
        chunks.append(
            {
                "id": str(chunk_id),
                "document_id": str(document_id),
                "knowledge_base_id": str(document["knowledge_base_id"]),
                "source": document["name"],
                "location_label": f"第 {page} 页"
                if page
                else f"Chunk #{number + 1}",
                "content": chunk.page_content,
                "content_hash": hashlib.sha256(
                    chunk.page_content.encode("utf-8")
                ).hexdigest(),
                "metadata": chunk.metadata,
                "chunk_no": number,
            }
        )

    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "DELETE FROM document_chunks WHERE document_id=$1", document_id
        )
        await conn.executemany(
            """INSERT INTO document_chunks
               (id, document_id, chunk_no, content, content_hash,
                location_label, token_count, metadata)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)""",
            [
                (
                    UUID(chunk["id"]),
                    document_id,
                    chunk["chunk_no"],
                    chunk["content"],
                    chunk["content_hash"],
                    chunk["location_label"],
                    len(chunk["content"]),
                    json.dumps(chunk["metadata"]),
                )
                for chunk in chunks
            ],
        )
    await _set_progress(document_id, task_id, "embedding", 70)

    if settings.MILVUS_INDEXING_ENABLED:
        await asyncio.to_thread(
            vector_index_service.delete_document, str(document_id)
        )
        await asyncio.to_thread(vector_index_service.index_chunks, chunks)
        await db.execute(
            "UPDATE document_chunks SET milvus_pk=id::text WHERE document_id=$1",
            (document_id,),
        )

    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """UPDATE documents SET status='ready', chunk_count=$2,
                      error_code=NULL, error_message=NULL, last_updated=now()
               WHERE id=$1""",
            document_id,
            len(chunks),
        )
        await conn.execute(
            """UPDATE document_tasks SET status='completed', progress=100,
                      completed_at=now(), error_code=NULL, error_message=NULL,
                      worker_id=NULL, lease_expires_at=NULL
               WHERE id=$1""",
            task_id,
        )
        await conn.execute(
            """UPDATE knowledge_bases kb SET document_count=(
                   SELECT count(*) FROM documents d
                   WHERE d.knowledge_base_id=kb.id AND d.deleted_at IS NULL
                     AND d.status='ready') WHERE kb.id=$1""",
            document["knowledge_base_id"],
        )


async def task_status(document_id: UUID, current_user: dict) -> dict:
    async with db.postgres_pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""SELECT t.id AS task_id, t.task_type, t.status, t.progress,
                      t.attempt_count, t.max_attempts, t.available_at,
                      t.heartbeat_at, t.error_code, t.error_message,
                      t.started_at, t.completed_at
               FROM document_tasks t
               JOIN documents d ON d.id=t.document_id
               JOIN knowledge_bases kb ON kb.id=d.knowledge_base_id
               JOIN knowledge_base_members m ON m.knowledge_base_id=kb.id
               WHERE d.id=$1 AND kb.organization_id=$2 AND m.user_id=$3
                 AND {document_access_sql("d", "$4", "$5")}
               ORDER BY t.created_at DESC LIMIT 1""",
            document_id,
            current_user["organization_id"],
            current_user["id"],
            current_user.get("security_level", "public"),
            current_user.get("team"),
        )
    if not row:
        raise _error(404, "DOCUMENT_NOT_FOUND", "文档不存在")
    return dict(row)


async def retry_document_task(
    document_id: UUID, current_user: dict, request_id: str
) -> dict:
    """Requeue the latest failed/dead task after an authorized manual action."""
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        document = await _accessible_document(conn, document_id, current_user)
        if not document:
            raise _error(404, "DOCUMENT_NOT_FOUND", "文档不存在")
        await require_role(conn, document["knowledge_base_id"], current_user, "editor")
        active = await conn.fetchval(
            """SELECT 1 FROM document_tasks
               WHERE document_id=$1 AND status IN ('pending','running','retry_wait')""",
            document_id,
        )
        if active:
            raise _error(409, "DOCUMENT_PROCESSING", "文档任务正在处理中")
        task = await conn.fetchrow(
            """SELECT id, task_type, status FROM document_tasks
               WHERE document_id=$1 ORDER BY created_at DESC LIMIT 1 FOR UPDATE""",
            document_id,
        )
        if not task or task["status"] not in {"failed", "dead"}:
            raise _error(409, "TASK_NOT_RETRYABLE", "当前没有可重试的失败任务")
        await conn.execute(
            """UPDATE document_tasks
               SET status='pending', progress=0, attempt_count=0,
                   available_at=now(), heartbeat_at=NULL, lease_expires_at=NULL,
                   worker_id=NULL, error_code=NULL, error_message=NULL,
                   started_at=NULL, completed_at=NULL
               WHERE id=$1""",
            task["id"],
        )
        if task["task_type"] != "delete":
            await conn.execute(
                """UPDATE documents SET status='uploaded', error_code=NULL,
                          error_message=NULL WHERE id=$1""",
                document_id,
            )
        await _audit(conn, current_user, "document.task_retried", document_id, request_id)
    return {
        "id": document_id,
        "task_id": task["id"],
        "task_type": task["task_type"],
        "status": "pending",
    }


async def update_document(
    document_id: UUID,
    values: dict,
    current_user: dict,
    request_id: str,
) -> dict:
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        document = await _accessible_document(conn, document_id, current_user)
        if not document:
            raise _error(404, "DOCUMENT_NOT_FOUND", "文档不存在")
        await require_role(conn, document["knowledge_base_id"], current_user, "editor")
        name = values.get("name", document["name"])
        metadata = values.get("metadata", document["metadata"])
        await conn.execute(
            "UPDATE documents SET name=$2, metadata=$3::jsonb WHERE id=$1",
            document_id,
            name,
            json.dumps(metadata),
        )
        await _audit(conn, current_user, "document.updated", document_id, request_id)
    return {"id": document_id, "name": name, "metadata": metadata}


async def reindex_document(
    document_id: UUID,
    force: bool,
    current_user: dict,
    request_id: str,
) -> dict:
    task_id = uuid4()
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        document = await _accessible_document(conn, document_id, current_user)
        if not document:
            raise _error(404, "DOCUMENT_NOT_FOUND", "文档不存在")
        role = await require_role(
            conn, document["knowledge_base_id"], current_user, "editor"
        )
        active_status = await conn.fetchval(
            "SELECT status FROM document_tasks WHERE document_id=$1 AND status IN ('pending','running','retry_wait')",
            document_id,
        )
        if active_status == "running":
            raise _error(409, "DOCUMENT_PROCESSING", "运行中的任务不能被替换，请等待完成或超时恢复")
        if active_status and not (force and role["role"] == "admin"):
            raise _error(409, "DOCUMENT_PROCESSING", "文档正在处理中")
        if active_status:
            await conn.execute(
                """UPDATE document_tasks SET status='failed', error_code='TASK_REPLACED',
                          error_message='管理员强制重新索引', completed_at=now()
                   WHERE document_id=$1 AND status IN ('pending','running','retry_wait')""",
                document_id,
            )
        await conn.execute(
            """INSERT INTO document_tasks
               (id, document_id, task_type, max_attempts, timeout_seconds)
               VALUES ($1,$2,'reindex',$3,$4)""",
            task_id,
            document_id,
            settings.DOCUMENT_TASK_MAX_ATTEMPTS,
            settings.DOCUMENT_TASK_TIMEOUT_SECONDS,
        )
        await _audit(conn, current_user, "document.reindexed", document_id, request_id)
    return {"id": document_id, "status": "pending", "task_id": task_id}


async def list_chunks(
    document_id: UUID, current_user: dict, cursor: int | None, limit: int
) -> dict:
    async with db.postgres_pool.acquire() as conn:
        document = await _accessible_document(conn, document_id, current_user)
        if not document:
            raise _error(404, "DOCUMENT_NOT_FOUND", "文档不存在")
        await require_role(conn, document["knowledge_base_id"], current_user, "editor")
        rows = await conn.fetch(
            """SELECT id, chunk_no, location_label, content, token_count,
                      (milvus_pk IS NOT NULL) AS indexed
               FROM document_chunks WHERE document_id=$1 AND chunk_no>$2
               ORDER BY chunk_no LIMIT $3""",
            document_id,
            cursor if cursor is not None else -1,
            limit + 1,
        )
    items = [dict(row) for row in rows[:limit]]
    return {
        "items": items,
        "next_cursor": items[-1]["chunk_no"] if len(rows) > limit else None,
    }


async def delete_document(
    document_id: UUID,
    current_user: dict,
    request_id: str,
) -> dict:
    task_id = uuid4()
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        document = await _accessible_document(conn, document_id, current_user)
        if not document:
            raise _error(404, "DOCUMENT_NOT_FOUND", "文档不存在")
        await require_role(conn, document["knowledge_base_id"], current_user, "editor")
        active = await conn.fetchval(
            "SELECT 1 FROM document_tasks WHERE document_id=$1 AND status IN ('pending','running','retry_wait')",
            document_id,
        )
        if active:
            raise _error(409, "DOCUMENT_PROCESSING", "文档正在处理中")
        await conn.execute(
            "UPDATE documents SET status='deleted', deleted_at=now() WHERE id=$1",
            document_id,
        )
        await conn.execute(
            """INSERT INTO document_tasks
               (id, document_id, task_type, max_attempts, timeout_seconds)
               VALUES ($1,$2,'delete',$3,$4)""",
            task_id,
            document_id,
            settings.DOCUMENT_TASK_MAX_ATTEMPTS,
            settings.DOCUMENT_TASK_TIMEOUT_SECONDS,
        )
        await _audit(conn, current_user, "document.deleted", document_id, request_id)
    return {"id": document_id, "status": "deleted", "task_id": task_id}


async def cleanup_document(
    document_id: UUID,
    task_id: UUID,
) -> None:
    document = await db.fetch_one(
        "SELECT knowledge_base_id, storage_key FROM documents WHERE id=$1",
        (document_id,),
    )
    if not document:
        raise RuntimeError("待清理文档不存在")
    await db.execute(
        "UPDATE document_tasks SET status='running', progress=20 WHERE id=$1",
        (task_id,),
    )
    await asyncio.to_thread(object_storage_service.delete, document["storage_key"])
    if settings.MILVUS_INDEXING_ENABLED:
        await asyncio.to_thread(vector_index_service.delete_document, str(document_id))
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """DELETE FROM document_chunks chunk
               WHERE chunk.document_id=$1
                 AND NOT EXISTS (
                     SELECT 1 FROM message_citations citation
                     WHERE citation.chunk_id=chunk.id
                 )""",
            document_id,
        )
        await conn.execute(
            """UPDATE document_tasks SET status='completed', progress=100,
                      completed_at=now(), error_code=NULL, error_message=NULL,
                      worker_id=NULL, lease_expires_at=NULL
               WHERE id=$1""",
            task_id,
        )
        await conn.execute(
            """UPDATE knowledge_bases kb SET document_count=(
                   SELECT count(*) FROM documents d
                   WHERE d.knowledge_base_id=kb.id AND d.deleted_at IS NULL
                     AND d.status='ready') WHERE kb.id=$1""",
            document["knowledge_base_id"],
        )
