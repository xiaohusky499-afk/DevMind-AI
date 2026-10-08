"""知识库与成员权限服务。"""

import json
from uuid import UUID

from asyncpg import UniqueViolationError
from fastapi import HTTPException

from ..core.db import db

_ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}

_SUMMARY_SQL = """
SELECT kb.id, kb.name, kb.description, kb.status, member.role,
       (SELECT count(*) FROM documents d
        WHERE d.knowledge_base_id=kb.id AND d.deleted_at IS NULL) AS document_count,
       (SELECT COALESCE(sum(d.chunk_count), 0) FROM documents d
        WHERE d.knowledge_base_id=kb.id AND d.deleted_at IS NULL) AS chunk_count,
       (SELECT count(*) FROM knowledge_base_members km
        WHERE km.knowledge_base_id=kb.id) AS member_count,
       kb.updated_at, kb.created_at
FROM knowledge_bases kb
JOIN knowledge_base_members member
  ON member.knowledge_base_id=kb.id AND member.user_id=$2
WHERE kb.id=$1 AND kb.organization_id=$3 AND kb.deleted_at IS NULL
"""


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


async def require_role(
    conn, knowledge_base_id: UUID, current_user: dict, minimum: str = "viewer"
) -> dict:
    row = await conn.fetchrow(
        """SELECT kb.id, kb.name, kb.status, member.role
           FROM knowledge_bases kb
           JOIN knowledge_base_members member ON member.knowledge_base_id=kb.id
           WHERE kb.id=$1 AND kb.organization_id=$2 AND kb.deleted_at IS NULL
             AND member.user_id=$3""",
        knowledge_base_id,
        current_user["organization_id"],
        current_user["id"],
    )
    if not row:
        raise _http_error(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在")
    if _ROLE_RANK[row["role"]] < _ROLE_RANK[minimum]:
        raise _http_error(
            403, "KNOWLEDGE_BASE_ROLE_REQUIRED", f"该操作需要 {minimum} 权限"
        )
    return dict(row)


async def _audit(
    conn,
    current_user: dict,
    action: str,
    resource_id: UUID,
    request_id: str,
    metadata: dict | None = None,
) -> None:
    await conn.execute(
        """INSERT INTO audit_logs
           (organization_id, user_id, action, resource_type, resource_id, request_id, metadata)
           VALUES ($1, $2, $3, 'knowledge_base', $4, $5, $6::jsonb)""",
        current_user["organization_id"],
        current_user["id"],
        action,
        str(resource_id),
        request_id,
        json.dumps(metadata or {}),
    )


async def list_knowledge_bases(
    current_user: dict,
    keyword: str | None = None,
    status: str | None = None,
    cursor: UUID | None = None,
    limit: int = 20,
) -> dict:
    params: list = [current_user["organization_id"], current_user["id"]]
    where = ["kb.organization_id=$1", "member.user_id=$2", "kb.deleted_at IS NULL"]

    async with db.postgres_pool.acquire() as conn:
        if keyword:
            params.append(f"%{keyword.strip()}%")
            where.append(
                f"(kb.name ILIKE ${len(params)} OR kb.description ILIKE ${len(params)})"
            )
        if status:
            params.append(status)
            where.append(f"kb.status=${len(params)}")
        if cursor:
            cursor_row = await conn.fetchrow(
                """SELECT kb.updated_at, kb.id FROM knowledge_bases kb
                   JOIN knowledge_base_members member ON member.knowledge_base_id=kb.id
                   WHERE kb.id=$1 AND kb.organization_id=$2 AND member.user_id=$3""",
                cursor,
                current_user["organization_id"],
                current_user["id"],
            )
            if not cursor_row:
                raise _http_error(400, "INVALID_CURSOR", "分页游标无效")
            params.extend([cursor_row["updated_at"], cursor_row["id"]])
            where.append(
                f"(kb.updated_at, kb.id) < (${len(params) - 1}, ${len(params)})"
            )

        params.append(limit + 1)
        rows = await conn.fetch(
            f"""SELECT kb.id, kb.name, kb.description, kb.status, member.role,
                       (SELECT count(*) FROM documents d
                        WHERE d.knowledge_base_id=kb.id AND d.deleted_at IS NULL) AS document_count,
                       (SELECT COALESCE(sum(d.chunk_count), 0) FROM documents d
                        WHERE d.knowledge_base_id=kb.id AND d.deleted_at IS NULL) AS chunk_count,
                       (SELECT count(*) FROM knowledge_base_members km
                        WHERE km.knowledge_base_id=kb.id) AS member_count,
                       kb.updated_at, kb.created_at
                FROM knowledge_bases kb
                JOIN knowledge_base_members member ON member.knowledge_base_id=kb.id
                WHERE {" AND ".join(where)}
                ORDER BY kb.updated_at DESC, kb.id DESC
                LIMIT ${len(params)}""",
            *params,
        )

    has_more = len(rows) > limit
    items = [dict(row) for row in rows[:limit]]
    return {"items": items, "next_cursor": str(items[-1]["id"]) if has_more else None}


async def get_knowledge_base(knowledge_base_id: UUID, current_user: dict) -> dict:
    async with db.postgres_pool.acquire() as conn:
        await require_role(conn, knowledge_base_id, current_user)
        row = await conn.fetchrow(
            _SUMMARY_SQL,
            knowledge_base_id,
            current_user["id"],
            current_user["organization_id"],
        )
    return dict(row)


async def create_knowledge_base(
    values: dict, current_user: dict, request_id: str
) -> dict:
    try:
        async with db.postgres_pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """INSERT INTO knowledge_bases
                   (organization_id, name, description, status, created_by)
                   VALUES ($1, $2, $3, 'ready', $4)
                   RETURNING id""",
                current_user["organization_id"],
                values["name"],
                values.get("description", ""),
                current_user["id"],
            )
            await conn.execute(
                """INSERT INTO knowledge_base_members (knowledge_base_id, user_id, role)
                   VALUES ($1, $2, 'admin')""",
                row["id"],
                current_user["id"],
            )
            await _audit(
                conn, current_user, "knowledge_base.created", row["id"], request_id
            )
    except UniqueViolationError as exc:
        raise _http_error(
            409, "KNOWLEDGE_BASE_NAME_EXISTS", "同一组织内知识库名称不能重复"
        ) from exc
    return await get_knowledge_base(row["id"], current_user)


async def update_knowledge_base(
    knowledge_base_id: UUID,
    values: dict,
    current_user: dict,
    request_id: str,
) -> dict:
    try:
        async with db.postgres_pool.acquire() as conn, conn.transaction():
            await require_role(conn, knowledge_base_id, current_user, "admin")
            if values:
                params: list = [knowledge_base_id, current_user["organization_id"]]
                assignments = []
                for field in ("name", "description", "status"):
                    if field in values:
                        params.append(values[field])
                        assignments.append(f"{field}=${len(params)}")
                await conn.execute(
                    f"UPDATE knowledge_bases SET {', '.join(assignments)} WHERE id=$1 AND organization_id=$2",
                    *params,
                )
            await _audit(
                conn,
                current_user,
                "knowledge_base.updated",
                knowledge_base_id,
                request_id,
                {"changed_fields": sorted(values)},
            )
    except UniqueViolationError as exc:
        raise _http_error(
            409, "KNOWLEDGE_BASE_NAME_EXISTS", "同一组织内知识库名称不能重复"
        ) from exc
    return await get_knowledge_base(knowledge_base_id, current_user)


async def archive_knowledge_base(
    knowledge_base_id: UUID, current_user: dict, request_id: str
) -> dict:
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await require_role(conn, knowledge_base_id, current_user, "admin")
        busy = await conn.fetchval(
            """SELECT 1 FROM documents
               WHERE knowledge_base_id=$1 AND deleted_at IS NULL
                 AND status IN ('uploaded', 'parsing', 'chunking', 'embedding', 'indexing')
               LIMIT 1""",
            knowledge_base_id,
        )
        if busy:
            raise _http_error(409, "KNOWLEDGE_BASE_BUSY", "知识库仍有文档任务正在执行")
        await conn.execute(
            """UPDATE knowledge_bases
               SET status='archived', deleted_at=now()
               WHERE id=$1 AND organization_id=$2""",
            knowledge_base_id,
            current_user["organization_id"],
        )
        await _audit(
            conn, current_user, "knowledge_base.archived", knowledge_base_id, request_id
        )
    return {"id": knowledge_base_id, "status": "archived"}


async def list_members(knowledge_base_id: UUID, current_user: dict) -> dict:
    async with db.postgres_pool.acquire() as conn:
        await require_role(conn, knowledge_base_id, current_user, "admin")
        rows = await conn.fetch(
            """SELECT u.id AS user_id, u.username,
                      COALESCE(p.display_name, u.nickname, u.username) AS display_name,
                      p.department_name, p.job_title, member.role, member.created_at
               FROM knowledge_base_members member
               JOIN users u ON u.id=member.user_id
               LEFT JOIN user_profiles p ON p.user_id=u.id
               WHERE member.knowledge_base_id=$1 AND u.organization_id=$2
               ORDER BY CASE member.role WHEN 'admin' THEN 1 WHEN 'editor' THEN 2 ELSE 3 END,
                        display_name""",
            knowledge_base_id,
            current_user["organization_id"],
        )
    return {"items": [dict(row) for row in rows]}


async def set_member_role(
    knowledge_base_id: UUID,
    target_user_id: UUID,
    role: str,
    current_user: dict,
    request_id: str,
) -> dict:
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await require_role(conn, knowledge_base_id, current_user, "admin")
        target = await conn.fetchrow(
            """SELECT id, username FROM users
               WHERE id=$1 AND organization_id=$2 AND is_active=true AND status='active'""",
            target_user_id,
            current_user["organization_id"],
        )
        if not target:
            raise _http_error(404, "ORGANIZATION_USER_NOT_FOUND", "组织成员不存在")

        existing_role = await conn.fetchval(
            """SELECT role FROM knowledge_base_members
               WHERE knowledge_base_id=$1 AND user_id=$2""",
            knowledge_base_id,
            target_user_id,
        )
        if existing_role == "admin" and role != "admin":
            admin_count = await conn.fetchval(
                "SELECT count(*) FROM knowledge_base_members WHERE knowledge_base_id=$1 AND role='admin'",
                knowledge_base_id,
            )
            if admin_count == 1:
                raise _http_error(
                    409, "LAST_ADMIN_REQUIRED", "知识库至少需要一名管理员"
                )

        await conn.execute(
            """INSERT INTO knowledge_base_members (knowledge_base_id, user_id, role)
               VALUES ($1, $2, $3)
               ON CONFLICT (knowledge_base_id, user_id)
               DO UPDATE SET role=EXCLUDED.role""",
            knowledge_base_id,
            target_user_id,
            role,
        )
        await _audit(
            conn,
            current_user,
            "knowledge_base.member_set",
            knowledge_base_id,
            request_id,
            {"target_user_id": str(target_user_id), "role": role},
        )
    return {"user_id": target_user_id, "username": target["username"], "role": role}


async def remove_member(
    knowledge_base_id: UUID,
    target_user_id: UUID,
    current_user: dict,
    request_id: str,
) -> dict:
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await require_role(conn, knowledge_base_id, current_user, "admin")
        target_role = await conn.fetchval(
            "SELECT role FROM knowledge_base_members WHERE knowledge_base_id=$1 AND user_id=$2",
            knowledge_base_id,
            target_user_id,
        )
        if not target_role:
            raise _http_error(
                404, "KNOWLEDGE_BASE_MEMBER_NOT_FOUND", "知识库成员不存在"
            )
        if target_role == "admin":
            admin_count = await conn.fetchval(
                "SELECT count(*) FROM knowledge_base_members WHERE knowledge_base_id=$1 AND role='admin'",
                knowledge_base_id,
            )
            if admin_count == 1:
                raise _http_error(
                    409, "LAST_ADMIN_REQUIRED", "知识库至少需要一名管理员"
                )
        await conn.execute(
            "DELETE FROM knowledge_base_members WHERE knowledge_base_id=$1 AND user_id=$2",
            knowledge_base_id,
            target_user_id,
        )
        await _audit(
            conn,
            current_user,
            "knowledge_base.member_removed",
            knowledge_base_id,
            request_id,
            {"target_user_id": str(target_user_id)},
        )
    return {"user_id": target_user_id, "removed": True}
