"""知识库与 FAQ 服务。

FAQ 搜索走 Redis 问题缓存：
- 有缓存直接返回，不打 PostgreSQL
- 没有缓存且 PostgreSQL 查到，写入随机 TTL 缓存
- PostgreSQL 也查不到，写空值短缓存，防止缓存穿透打爆 PostgreSQL
- 并发热点问题使用 Redis 锁 + 指数退避，防止缓存击穿
"""

from uuid import UUID

from fastapi import HTTPException

from ..core import cache
from ..core.access_control import document_access_sql
from ..core.db import db

# ---------------- 文档知识库 ----------------


def _bind(params: list, value) -> str:
    params.append(value)
    return f"${len(params)}"


async def list_documents(
    current_user: dict,
    page: int,
    page_size: int,
    keyword: str | None = None,
    doc_type: str | None = None,
    team: str | None = None,
) -> dict:
    where = [
        "kb.organization_id=$1",
        "member.user_id=$2",
        "d.deleted_at IS NULL",
        "kb.deleted_at IS NULL",
        document_access_sql("d", "$3", "$4"),
    ]
    params = [
        current_user["organization_id"],
        current_user["id"],
        current_user.get("security_level", "public"),
        current_user.get("team"),
    ]
    if keyword:
        marker = _bind(params, f"%{keyword}%")
        where.append(f"(d.name ILIKE {marker} OR d.doc_source ILIKE {marker})")
    if doc_type:
        where.append(f"d.doc_type = {_bind(params, doc_type)}")
    if team:
        where.append(f"d.team = {_bind(params, team)}")

    where_sql = f"WHERE {' AND '.join(where)}"
    offset = (page - 1) * page_size

    joins = """FROM documents d
               JOIN knowledge_bases kb ON kb.id=d.knowledge_base_id
               JOIN knowledge_base_members member ON member.knowledge_base_id=kb.id"""
    total_row = await db.fetch_one(
        f"SELECT COUNT(*) AS total {joins} {where_sql}", tuple(params)
    )
    rows = await db.fetch_all(
        f"""SELECT d.id, d.knowledge_base_id, kb.name AS knowledge_base_name,
                   d.name, d.doc_source, d.source_type, d.doc_type, d.mime_type,
                   d.size_bytes, d.team, d.system_name, d.version, d.security_level,
                   d.status, d.chunk_count, d.last_updated, d.created_at, d.updated_at,
                   d.error_code, d.error_message, member.role
            {joins} {where_sql} ORDER BY d.updated_at DESC
            LIMIT {_bind(params, page_size)} OFFSET {_bind(params, offset)}""",
        tuple(params),
    )
    return {"total": total_row["total"], "items": rows}


async def get_document(doc_id: UUID, current_user: dict) -> dict:
    row = await db.fetch_one(
        f"""SELECT d.id, d.knowledge_base_id, kb.name AS knowledge_base_name,
                  d.name, d.doc_source, d.source_type, d.doc_type, d.mime_type,
                  d.size_bytes, d.team, d.system_name, d.version, d.security_level,
                  d.status, d.chunk_count, d.last_updated, d.created_at, d.updated_at,
                  d.error_code, d.error_message, member.role
           FROM documents d
           JOIN knowledge_bases kb ON kb.id=d.knowledge_base_id
           JOIN knowledge_base_members member ON member.knowledge_base_id=kb.id
           WHERE d.id=$1 AND kb.organization_id=$2 AND member.user_id=$3
             AND {document_access_sql("d", "$4", "$5")}
             AND d.deleted_at IS NULL AND kb.deleted_at IS NULL""",
        (
            doc_id,
            current_user["organization_id"],
            current_user["id"],
            current_user.get("security_level", "public"),
            current_user.get("team"),
        ),
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"code": "DOCUMENT_NOT_FOUND", "message": "文档不存在"},
        )
    return row


# ---------------- 高频 FAQ ----------------


async def list_faq(
    current_user: dict,
    page: int,
    page_size: int,
    keyword: str | None = None,
    category: str | None = None,
    team: str | None = None,
) -> dict:
    where = ["organization_id=$1", document_access_sql("faq", "$2", "$3")]
    params = [
        current_user["organization_id"],
        current_user.get("security_level", "public"),
        current_user.get("team"),
    ]
    if keyword:
        marker = _bind(params, f"%{keyword}%")
        where.append(
            f"(question ILIKE {marker} OR keywords ILIKE {marker} OR answer ILIKE {marker})"
        )
    if category:
        where.append(f"category = {_bind(params, category)}")
    if team:
        where.append(f"team = {_bind(params, team)}")

    where.append("is_active = TRUE")
    where_sql = f"WHERE {' AND '.join(where)}"
    offset = (page - 1) * page_size

    total_row = await db.fetch_one(
        f"SELECT COUNT(*) AS total FROM faq {where_sql}", tuple(params)
    )
    rows = await db.fetch_all(
        f"""SELECT id, question, answer, doc_source, category, team, system_name, security_level, version, last_updated
            FROM faq {where_sql} ORDER BY updated_at DESC
            LIMIT {_bind(params, page_size)} OFFSET {_bind(params, offset)}""",
        tuple(params),
    )
    return {"total": total_row["total"], "items": rows}


async def get_faq(faq_id: int, current_user: dict) -> dict:
    row = await db.fetch_one(
        f"""SELECT id, question, answer, doc_source, category, team, system_name, security_level, version, last_updated
           FROM faq WHERE id=$1 AND organization_id=$2 AND is_active=TRUE
             AND {document_access_sql("faq", "$3", "$4")}""",
        (
            faq_id,
            current_user["organization_id"],
            current_user.get("security_level", "public"),
            current_user.get("team"),
        ),
    )
    if not row:
        raise HTTPException(status_code=404, detail="FAQ 不存在")
    return row


async def search_faq_question(question: str, current_user: dict) -> dict:
    """用户问题查询：Redis 缓存 -> PostgreSQL -> 空值缓存。"""
    question = question.strip()
    if not question:
        return {"found": False, "cached": False, "message": "问题不能为空"}

    # 1. 先查 Redis 缓存
    scope = ":".join(
        str(value)
        for value in (
            current_user["organization_id"],
            current_user.get("security_level", "public"),
            current_user.get("team", ""),
        )
    )
    exists, cached_data = await cache.get_question_cache(question, scope)
    if exists:
        if cached_data is None:
            return {"found": False, "cached": True}
        cached_data["cached"] = True
        return cached_data

    # 2. 尝试获取重建锁，避免热点问题同时打 PostgreSQL
    lock_ok = await cache.acquire_question_lock(question, scope)
    if not lock_ok:
        # 3. 拿不到锁就指数退避等待其他请求写入缓存
        exists, cached_data = await cache.wait_and_get_cache(question, scope)
        if exists:
            if cached_data is None:
                return {"found": False, "cached": True}
            cached_data["cached"] = True
            return cached_data
        # 退避后仍未命中，直接查库兜底（不让请求无限等待）
        return await _query_and_cache(question, current_user, scope, cached=False)

    try:
        # 4. 拿到锁后二次检查缓存，避免重复查库
        exists, cached_data = await cache.get_question_cache(question, scope)
        if exists:
            if cached_data is None:
                return {"found": False, "cached": True}
            cached_data["cached"] = True
            return cached_data
        return await _query_and_cache(question, current_user, scope, cached=False)
    finally:
        await cache.release_question_lock(question, scope)


async def _query_and_cache(
    question: str, current_user: dict, scope: str, cached: bool = False
) -> dict:
    """查询 PostgreSQL，并把结果写回 Redis。"""
    # 先精确匹配，再尝试 LIKE 模糊匹配
    row = await db.fetch_one(
        f"""SELECT * FROM faq WHERE question=$1 AND organization_id=$2
              AND is_active=TRUE AND {document_access_sql("faq", "$3", "$4")}
              LIMIT 1""",
        (
            question,
            current_user["organization_id"],
            current_user.get("security_level", "public"),
            current_user.get("team"),
        ),
    )
    if not row:
        row = await db.fetch_one(
            f"""SELECT * FROM faq
               WHERE question ILIKE $1 AND organization_id=$2 AND is_active=TRUE
                 AND {document_access_sql("faq", "$3", "$4")}
               ORDER BY char_length(question) ASC LIMIT 1""",
            (
                f"%{question}%",
                current_user["organization_id"],
                current_user.get("security_level", "public"),
                current_user.get("team"),
            ),
        )

    if not row:
        # 缓存穿透保护：查不到也写空缓存
        await cache.set_question_empty(question, scope)
        return {"found": False, "cached": cached}

    result = {
        "found": True,
        "cached": cached,
        "question": row["question"],
        "answer": row["answer"],
        "source": row.get("doc_source"),
        "category": row.get("category"),
        "team": row.get("team"),
        "security_level": row.get("security_level"),
    }
    await cache.set_question_cache(question, result, scope)
    result["cached"] = False
    return result
