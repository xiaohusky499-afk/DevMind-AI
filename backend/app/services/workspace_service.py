"""工作台收藏和用户偏好持久化。"""

import json
from uuid import UUID

from fastapi import HTTPException

from ..core.db import db
from .rag_service import accessible_knowledge_bases


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


async def _owned_assistant(conn, message_id: UUID, current_user: dict) -> dict:
    row = await conn.fetchrow(
        """SELECT m.id, m.session_id FROM chat_messages m
           JOIN chat_sessions s ON s.id=m.session_id
           WHERE m.id=$1 AND m.role='assistant' AND s.organization_id=$2
             AND s.user_id=$3 AND s.deleted_at IS NULL""",
        message_id,
        current_user["organization_id"],
        current_user["id"],
    )
    if not row:
        raise _error(404, "MESSAGE_NOT_FOUND", "回答不存在或不可收藏")
    return dict(row)


async def set_favorite(
    message_id: UUID, favorite: bool, current_user: dict, request_id: str
) -> dict:
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await _owned_assistant(conn, message_id, current_user)
        if favorite:
            await conn.execute(
                """INSERT INTO message_favorites (user_id,message_id) VALUES ($1,$2)
                   ON CONFLICT (user_id,message_id) DO NOTHING""",
                current_user["id"],
                message_id,
            )
        else:
            await conn.execute(
                "DELETE FROM message_favorites WHERE user_id=$1 AND message_id=$2",
                current_user["id"],
                message_id,
            )
        await conn.execute(
            """INSERT INTO audit_logs
               (organization_id,user_id,action,resource_type,resource_id,request_id)
               VALUES ($1,$2,$3,'chat_message',$4,$5)""",
            current_user["organization_id"],
            current_user["id"],
            "message.favorited" if favorite else "message.unfavorited",
            str(message_id),
            request_id,
        )
    return {"message_id": message_id, "favorited": favorite}


async def list_favorites(
    current_user: dict, cursor: UUID | None, limit: int, keyword: str | None
) -> dict:
    params: list = [current_user["id"], current_user["organization_id"]]
    conditions = ["f.user_id=$1", "s.organization_id=$2", "s.deleted_at IS NULL"]
    if keyword:
        params.append(f"%{keyword.strip()}%")
        conditions.append(
            f"(m.content ILIKE ${len(params)} OR question.content ILIKE ${len(params)} OR s.title ILIKE ${len(params)})"
        )
    if cursor:
        cursor_row = await db.fetch_one(
            "SELECT created_at,message_id FROM message_favorites WHERE user_id=$1 AND message_id=$2",
            (current_user["id"], cursor),
        )
        if not cursor_row:
            raise _error(400, "INVALID_CURSOR", "分页游标无效")
        params.extend([cursor_row["created_at"], cursor_row["message_id"]])
        conditions.append(
            f"(f.created_at,f.message_id)<(${len(params)-1},${len(params)})"
        )
    params.append(limit + 1)
    rows = await db.fetch_all(
        f"""SELECT m.id AS message_id,m.content,m.session_id,s.title AS session_title,
                   question.content AS question,f.created_at AS favorited_at,
                   m.citation_count,m.confidence
            FROM message_favorites f
            JOIN chat_messages m ON m.id=f.message_id
            JOIN chat_sessions s ON s.id=m.session_id
            LEFT JOIN chat_messages question ON question.id=m.parent_message_id
            WHERE {' AND '.join(conditions)}
            ORDER BY f.created_at DESC,f.message_id DESC LIMIT ${len(params)}""",
        tuple(params),
    )
    items = rows[:limit]
    return {
        "items": items,
        "next_cursor": str(items[-1]["message_id"]) if len(rows) > limit else None,
    }


async def get_preferences(current_user: dict) -> dict:
    row = await db.fetch_one(
        """SELECT default_knowledge_base_ids,default_mode,locale,timezone,answer_style,updated_at
           FROM user_preferences WHERE user_id=$1""",
        (current_user["id"],),
    )
    return row or {
        "default_knowledge_base_ids": [],
        "default_mode": "tech",
        "locale": "zh-CN",
        "timezone": "Asia/Shanghai",
        "answer_style": "balanced",
        "updated_at": None,
    }


async def update_preferences(values: dict, current_user: dict, request_id: str) -> dict:
    if "default_knowledge_base_ids" in values:
        requested = values["default_knowledge_base_ids"]
        values["default_knowledge_base_ids"] = (
            await accessible_knowledge_bases(current_user, requested)
            if requested
            else []
        )
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO user_preferences (user_id) VALUES ($1) ON CONFLICT (user_id) DO NOTHING",
            current_user["id"],
        )
        if values:
            assignments = ", ".join(
                f"{field}=${index}" for index, field in enumerate(values, start=2)
            )
            await conn.execute(
                f"UPDATE user_preferences SET {assignments},updated_at=now() WHERE user_id=$1",
                current_user["id"],
                *values.values(),
            )
        await conn.execute(
            """INSERT INTO audit_logs
               (organization_id,user_id,action,resource_type,resource_id,request_id,metadata)
               VALUES ($1,$2,'user.preferences.updated','user_preferences',$3,$4,$5::jsonb)""",
            current_user["organization_id"],
            current_user["id"],
            str(current_user["id"]),
            request_id,
            json.dumps({"changed_fields": sorted(values)}),
        )
    return await get_preferences(current_user)
