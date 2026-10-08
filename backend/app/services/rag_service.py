"""正式 RAG：会话、权限检索、重排、生成、引用持久化。"""

import asyncio
import json
import logging
import secrets
import time
from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

from fastapi import HTTPException
from openai import AsyncOpenAI

from internal_kb_qa.core.confidence import evaluate_confidence, select_evidence
from internal_kb_qa.core.hit import Hit
from internal_kb_qa.core.prompts import RAGPrompts
from internal_kb_qa.core.query_rewrite import query_rewrite
from internal_kb_qa.core.reranker import rerank

from ..core.access_control import document_access_sql
from ..core.config import settings
from ..core.db import db
from . import vector_index_service

_local_model_slots = asyncio.Semaphore(settings.RAG_LOCAL_MODEL_CONCURRENCY)
_llm_slots = asyncio.Semaphore(settings.RAG_LLM_CONCURRENCY)
logger = logging.getLogger(__name__)


def _error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _uuid(value: str, name: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as exc:
        raise _error(422, "INVALID_ID", f"{name} 格式不正确") from exc


async def accessible_knowledge_bases(
    current_user: dict, requested: list[str] | None = None
) -> list[UUID]:
    requested_ids = [
        _uuid(item, "knowledge_base_id") for item in dict.fromkeys(requested or [])
    ]
    params: list = [current_user["organization_id"], current_user["id"]]
    condition = ""
    if requested_ids:
        params.append(requested_ids)
        condition = "AND kb.id=ANY($3::uuid[])"
    rows = await db.fetch_all(
        f"""SELECT kb.id FROM knowledge_bases kb
            JOIN knowledge_base_members member ON member.knowledge_base_id=kb.id
            WHERE kb.organization_id=$1 AND member.user_id=$2
              AND kb.deleted_at IS NULL AND kb.status='ready' {condition}
            ORDER BY kb.updated_at DESC""",
        tuple(params),
    )
    ids = [row["id"] for row in rows]
    if requested_ids and len(ids) != len(requested_ids):
        raise _error(404, "KNOWLEDGE_BASE_NOT_FOUND", "知识库不存在或无权访问")
    return ids


async def accessible_documents(
    current_user: dict, knowledge_base_ids: list[UUID]
) -> list[UUID]:
    """返回当前用户在指定知识库内可读取的文档，用于检索前强制过滤。"""
    if not knowledge_base_ids:
        return []
    rows = await db.fetch_all(
        f"""SELECT d.id FROM documents d
            JOIN knowledge_bases kb ON kb.id=d.knowledge_base_id
            JOIN knowledge_base_members member ON member.knowledge_base_id=kb.id
            WHERE kb.organization_id=$1 AND member.user_id=$2
              AND d.knowledge_base_id=ANY($3::uuid[])
              AND d.deleted_at IS NULL AND d.status='ready'
              AND {document_access_sql("d", "$4", "$5")}""",
        (
            current_user["organization_id"],
            current_user["id"],
            knowledge_base_ids,
            current_user.get("security_level", "public"),
            current_user.get("team"),
        ),
    )
    return [row["id"] for row in rows]


async def create_session(values: dict, current_user: dict) -> dict:
    knowledge_base_ids = await accessible_knowledge_bases(
        current_user, values.get("knowledge_base_ids")
    )
    row = await db.fetch_one(
        """INSERT INTO chat_sessions
           (organization_id, user_id, title, knowledge_base_ids)
           VALUES ($1,$2,$3,$4) RETURNING id, title, updated_at""",
        (
            current_user["organization_id"],
            current_user["id"],
            values.get("title", "").strip() or "新建对话",
            knowledge_base_ids,
        ),
    )
    return row


async def list_sessions(
    current_user: dict,
    cursor: UUID | None,
    limit: int,
    keyword: str | None = None,
) -> dict:
    params: list = [current_user["organization_id"], current_user["id"]]
    conditions = []
    if keyword:
        params.append(f"%{keyword.strip()}%")
        conditions.append(
            f"(s.title ILIKE ${len(params)} OR EXISTS (SELECT 1 FROM chat_messages search_message WHERE search_message.session_id=s.id AND search_message.content ILIKE ${len(params)}))"
        )
    if cursor:
        cursor_row = await db.fetch_one(
            """SELECT last_message_at, id FROM chat_sessions
               WHERE id=$1 AND organization_id=$2 AND user_id=$3
                 AND deleted_at IS NULL""",
            (
                cursor,
                current_user["organization_id"],
                current_user["id"],
            ),
        )
        if not cursor_row:
            raise _error(400, "INVALID_CURSOR", "分页游标无效")
        params.extend([cursor_row["last_message_at"], cursor_row["id"]])
        conditions.append(
            f"(s.last_message_at,s.id)<(${len(params)-1},${len(params)})"
        )
    params.append(limit + 1)
    rows = await db.fetch_all(
        f"""SELECT s.id, s.title, s.knowledge_base_ids, s.last_message_at,
                   s.created_at, count(m.id)::int AS message_count,
                   (SELECT content FROM chat_messages preview
                    WHERE preview.session_id=s.id ORDER BY preview.created_at DESC LIMIT 1) AS preview
            FROM chat_sessions s
            LEFT JOIN chat_messages m ON m.session_id=s.id
            WHERE s.organization_id=$1 AND s.user_id=$2 AND s.deleted_at IS NULL
              {''.join(f' AND {condition}' for condition in conditions)}
            GROUP BY s.id ORDER BY s.last_message_at DESC,s.id DESC
            LIMIT ${len(params)}""",
        tuple(params),
    )
    items = rows[:limit]
    return {
        "items": items,
        "next_cursor": str(items[-1]["id"]) if len(rows) > limit else None,
    }


async def update_session(
    session_id: UUID, title: str, current_user: dict, request_id: str
) -> dict:
    await _session(session_id, current_user)
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE chat_sessions SET title=$2 WHERE id=$1",
            session_id,
            title,
        )
        await conn.execute(
            """INSERT INTO audit_logs
               (organization_id,user_id,action,resource_type,resource_id,request_id)
               VALUES ($1,$2,'chat.session.updated','chat_session',$3,$4)""",
            current_user["organization_id"],
            current_user["id"],
            str(session_id),
            request_id,
        )
    return await _session(session_id, current_user)


async def delete_session(session_id: UUID, current_user: dict, request_id: str) -> dict:
    await _session(session_id, current_user)
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE chat_sessions SET deleted_at=now() WHERE id=$1",
            session_id,
        )
        await conn.execute(
            """INSERT INTO audit_logs
               (organization_id,user_id,action,resource_type,resource_id,request_id)
               VALUES ($1,$2,'chat.session.deleted','chat_session',$3,$4)""",
            current_user["organization_id"],
            current_user["id"],
            str(session_id),
            request_id,
        )
    return {"id": session_id, "deleted": True}


async def _session(session_id: UUID, current_user: dict) -> dict:
    row = await db.fetch_one(
        """SELECT id, title, knowledge_base_ids, last_message_at, created_at
           FROM chat_sessions WHERE id=$1 AND organization_id=$2 AND user_id=$3
             AND deleted_at IS NULL""",
        (session_id, current_user["organization_id"], current_user["id"]),
    )
    if not row:
        raise _error(404, "SESSION_NOT_FOUND", "会话不存在")
    return row


async def get_session(session_id: UUID, current_user: dict) -> dict:
    session = await _session(session_id, current_user)
    session["messages"] = await db.fetch_all(
        """SELECT id, parent_message_id, role, content, mode, status,
                  confidence, citation_count, model, usage, error_code,
                  created_at, completed_at
           FROM chat_messages WHERE session_id=$1 ORDER BY created_at,id""",
        (session_id,),
    )
    return session


async def create_message(values: dict, current_user: dict) -> dict:
    session_id = _uuid(values["session_id"], "session_id")
    session = await _session(session_id, current_user)
    requested = values.get("knowledge_base_ids") or [
        str(item) for item in session["knowledge_base_ids"]
    ]
    knowledge_base_ids = await accessible_knowledge_bases(current_user, requested)
    user_message_id, assistant_message_id = uuid4(), uuid4()
    parent_id = (
        _uuid(values["parent_message_id"], "parent_message_id")
        if values.get("parent_message_id")
        else None
    )
    content = values["content"].strip()
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        if parent_id:
            exists = await conn.fetchval(
                "SELECT 1 FROM chat_messages WHERE id=$1 AND session_id=$2",
                parent_id,
                session_id,
            )
            if not exists:
                raise _error(404, "PARENT_MESSAGE_NOT_FOUND", "上级消息不存在")
        await conn.execute(
            """INSERT INTO chat_messages
               (id,session_id,parent_message_id,role,content,mode,status,completed_at)
               VALUES ($1,$2,$3,'user',$4,$5,'completed',now())""",
            user_message_id,
            session_id,
            parent_id,
            content,
            values["mode"],
        )
        await conn.execute(
            """INSERT INTO chat_messages
               (id,session_id,parent_message_id,role,mode,status,model)
               VALUES ($1,$2,$3,'assistant',$4,'pending',$5)""",
            assistant_message_id,
            session_id,
            user_message_id,
            values["mode"],
            settings.LLM_MODEL,
        )
        await conn.execute(
            """UPDATE chat_sessions SET knowledge_base_ids=$2,
                      title=CASE WHEN title='新建对话' THEN left($3,80) ELSE title END,
                      last_message_at=now() WHERE id=$1""",
            session_id,
            knowledge_base_ids,
            content,
        )
    ticket = secrets.token_urlsafe(32)
    await db.redis_token.set(
        f"chat:stream:{ticket}",
        json.dumps(
            {
                "message_id": str(assistant_message_id),
                "user_id": str(current_user["id"]),
                "organization_id": str(current_user["organization_id"]),
            }
        ),
        ex=300,
    )
    return {
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "stream_url": (
            f"/api/v1/chat/stream?message_id={assistant_message_id}&ticket={ticket}"
        ),
    }


async def consume_stream_ticket(ticket: str, message_id: UUID) -> dict:
    raw = await db.redis_token.getdel(f"chat:stream:{ticket}")
    if not raw:
        raise _error(401, "INVALID_STREAM_TICKET", "流式连接凭证无效或已过期")
    payload = json.loads(raw)
    if payload.get("message_id") != str(message_id):
        raise _error(401, "INVALID_STREAM_TICKET", "流式连接凭证与消息不匹配")
    user = await db.fetch_one(
        """SELECT id,organization_id,username,nickname,email,phone,team,security_level
           FROM users WHERE id=$1 AND organization_id=$2
             AND is_active=true AND status='active'""",
        (UUID(payload["user_id"]), UUID(payload["organization_id"])),
    )
    if not user:
        raise _error(401, "INVALID_STREAM_TICKET", "用户不存在或已被禁用")
    return user


async def get_message(message_id: UUID, current_user: dict) -> dict:
    row = await db.fetch_one(
        """SELECT m.id, m.session_id, m.parent_message_id, m.role, m.content,
                  m.mode, m.status, m.confidence, m.citation_count, m.model,
                  m.usage, m.error_code, m.created_at, m.completed_at
           FROM chat_messages m JOIN chat_sessions s ON s.id=m.session_id
           WHERE m.id=$1 AND s.organization_id=$2 AND s.user_id=$3
             AND s.deleted_at IS NULL""",
        (message_id, current_user["organization_id"], current_user["id"]),
    )
    if not row:
        raise _error(404, "MESSAGE_NOT_FOUND", "消息不存在")
    return row


async def _rewrite(query: str, history: list[dict]) -> str:
    if not history:
        return query
    rewritten = await asyncio.to_thread(
        query_rewrite,
        query,
        history,
        "技术咨询",
        "direct",
    )
    return rewritten.rewritten_query or query


def _rerank_candidates(hits: list[Hit], top_n: int) -> list[Hit]:
    return hits[: max(3, top_n)]


async def _run_model(semaphore: asyncio.Semaphore, function, *args):
    async with semaphore:
        return await asyncio.wait_for(
            asyncio.to_thread(function, *args),
            timeout=settings.RAG_MODEL_TIMEOUT_SECONDS,
        )


async def retrieve(
    query: str,
    current_user: dict,
    requested_kb_ids: list[str],
    top_k: int,
    rerank_top_n: int,
    message_id: UUID | None = None,
    history: list[dict] | None = None,
) -> dict:
    started = time.perf_counter()
    timings: dict[str, int] = {}
    stage_started = time.perf_counter()
    knowledge_base_ids = await accessible_knowledge_bases(
        current_user, requested_kb_ids
    )
    document_ids = await accessible_documents(current_user, knowledge_base_ids)
    timings["access_ms"] = round((time.perf_counter() - stage_started) * 1000)
    stage_started = time.perf_counter()
    rewritten_query = await _rewrite(query, history or [])
    timings["rewrite_ms"] = round((time.perf_counter() - stage_started) * 1000)
    search_timings: dict[str, int] = {}
    stage_started = time.perf_counter()
    raw = await _run_model(
        _local_model_slots,
        vector_index_service.hybrid_search,
        rewritten_query,
        [str(item) for item in knowledge_base_ids],
        top_k,
        search_timings,
        [str(item) for item in document_ids],
    )
    timings["search_ms"] = round((time.perf_counter() - stage_started) * 1000)
    timings.update(search_timings)
    retrieved = [
        Hit(
            text=item["text"],
            score=item["score"],
            metadata={key: value for key, value in item.items() if key != "text"},
        )
        for item in raw
    ]
    candidates = _rerank_candidates(retrieved, rerank_top_n)
    timings["rerank_input_count"] = len(candidates)
    stage_started = time.perf_counter()
    ranked = await _run_model(
        _local_model_slots, rerank, query, candidates, rerank_top_n
    )
    timings["rerank_ms"] = round((time.perf_counter() - stage_started) * 1000)
    latency_ms = int((time.perf_counter() - started) * 1000)
    run_id = uuid4()
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """INSERT INTO retrieval_runs
               (id,message_id,user_id,query,rewritten_query,top_k,rerank_top_n,
                retrieved_count,reranked_count,latency_ms)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
            run_id,
            message_id,
            current_user["id"],
            query,
            rewritten_query,
            top_k,
            rerank_top_n,
            len(retrieved),
            len(ranked),
            latency_ms,
        )
        rows = []
        for stage, hits in (("retrieve", retrieved), ("rerank", ranked)):
            rows.extend(
                (
                    uuid4(),
                    run_id,
                    UUID(hit.metadata["chunk_id"]),
                    stage,
                    index,
                    max(0.0, min(1.0, hit.score)),
                )
                for index, hit in enumerate(hits, 1)
            )
        if rows:
            await conn.executemany(
                """INSERT INTO retrieval_results
                   (id,retrieval_run_id,chunk_id,stage,rank,score)
                   VALUES ($1,$2,$3,$4,$5,$6)""",
                rows,
            )
        await conn.execute(
            """INSERT INTO audit_logs
               (organization_id,user_id,action,resource_type,resource_id,metadata)
               VALUES ($1,$2,'retrieval.executed','retrieval_run',$3,$4::jsonb)""",
            current_user["organization_id"],
            current_user["id"],
            str(run_id),
            json.dumps(
                {
                    "query_summary": query[:200],
                    "retrieved_count": len(retrieved),
                    "reranked_count": len(ranked),
                    "latency_ms": latency_ms,
                }
            ),
        )
    logger.info(
        "RAG retrieval completed",
        extra={
            "event": "rag.retrieval.completed",
            "duration_ms": latency_ms,
            "retrieved_count": len(retrieved),
            "reranked_count": len(ranked),
        },
    )
    return {
        "retrieval_run_id": run_id,
        "query": query,
        "rewritten_query": rewritten_query,
        "retrieved_count": len(retrieved),
        "reranked_count": len(ranked),
        "latency_ms": latency_ms,
        "timings": timings,
        "retrieved": retrieved,
        "ranked": ranked,
    }


def _context(hits: list[Hit]) -> str:
    return "\n\n".join(
        f"[来源: {hit.metadata['source']}, {hit.metadata['location_label']}]\n{hit.text}"
        for hit in hits
    )


def _history(messages: list[dict]) -> list[dict]:
    pairs, question = [], None
    for message in messages:
        if message["role"] == "user":
            question = message["content"]
        elif message["role"] == "assistant" and question and message["content"]:
            pairs.append({"question": question, "answer": message["content"]})
            question = None
    return pairs[-5:]


async def _persist_citations(message_id: UUID, hits: list[Hit]) -> list[dict]:
    async with db.postgres_pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "DELETE FROM message_citations WHERE message_id=$1", message_id
        )
        for rank, hit in enumerate(hits, 1):
            await conn.execute(
                """INSERT INTO message_citations
                   (message_id,chunk_id,rank,score,excerpt)
                   VALUES ($1,$2,$3,$4,$5)""",
                message_id,
                UUID(hit.metadata["chunk_id"]),
                rank,
                max(0.0, min(1.0, hit.score)),
                hit.text[:1000],
            )
    return await _citation_items(message_id)


async def _citation_items(
    message_id: UUID, current_user: dict | None = None
) -> list[dict]:
    access_join = ""
    access_where = ""
    params: tuple = (message_id,)
    if current_user:
        access_join = """JOIN knowledge_bases kb ON kb.id=d.knowledge_base_id
                         JOIN knowledge_base_members member
                           ON member.knowledge_base_id=kb.id"""
        access_where = f"""AND kb.organization_id=$2 AND member.user_id=$3
            AND {document_access_sql("d", "$4", "$5")}"""
        params = (
            message_id,
            current_user["organization_id"],
            current_user["id"],
            current_user.get("security_level", "public"),
            current_user.get("team"),
        )
    return await db.fetch_all(
        f"""SELECT c.id, d.id AS document_id, d.name AS document_name,
                  d.source_type AS document_type, ch.location_label,
                  ch.id AS chunk_id, c.score::float8 AS score, c.excerpt,
                  NULL::text AS url
           FROM message_citations c
           JOIN document_chunks ch ON ch.id=c.chunk_id
           JOIN documents d ON d.id=ch.document_id
           {access_join}
           WHERE c.message_id=$1 AND d.deleted_at IS NULL {access_where}
           ORDER BY c.rank""",
        params,
    )


async def answer_events(
    assistant_message_id: UUID, current_user: dict
) -> AsyncGenerator[dict, None]:
    assistant = await get_message(assistant_message_id, current_user)
    if assistant["role"] != "assistant":
        raise _error(422, "INVALID_ASSISTANT_MESSAGE", "目标不是 AI 回答消息")
    if assistant["status"] == "completed":
        if assistant["content"]:
            yield {"type": "token", "data": {"content": assistant["content"]}}
        items = await _citation_items(assistant_message_id, current_user)
        yield {"type": "citations", "data": {"items": items}}
        yield {
            "type": "final",
            "data": {
                "message_id": assistant_message_id,
                "confidence": assistant["confidence"],
                "citation_count": len(items),
                "usage": assistant["usage"],
            },
        }
        return
    claimed = await db.execute(
        """UPDATE chat_messages SET status='running'
           WHERE id=$1 AND status IN ('pending','failed')""",
        (assistant_message_id,),
    )
    if not claimed:
        raise _error(409, "MESSAGE_ALREADY_RUNNING", "回答正在生成中")

    source = await db.fetch_one(
        """SELECT user_message.content AS query, s.knowledge_base_ids
           FROM chat_messages assistant
           JOIN chat_messages user_message ON user_message.id=assistant.parent_message_id
           JOIN chat_sessions s ON s.id=assistant.session_id
           WHERE assistant.id=$1""",
        (assistant_message_id,),
    )
    history_rows = await db.fetch_all(
        """SELECT role,content FROM chat_messages
           WHERE session_id=$1 AND created_at<$2 ORDER BY created_at DESC LIMIT 10""",
        (assistant["session_id"], assistant["created_at"]),
    )
    history = _history(list(reversed(history_rows)))

    try:
        answer_started = time.perf_counter()
        yield {
            "type": "status",
            "data": {"stage": "analyzing", "message": "正在分析问题…"},
        }
        result = await retrieve(
            source["query"],
            current_user,
            [str(item) for item in source["knowledge_base_ids"]],
            settings.RETRIEVAL_TOP_K,
            settings.RERANK_TOP_K,
            assistant_message_id,
            history,
        )
        yield {
            "type": "retrieval",
            "data": {
                "stage": "recall",
                "message": f"已找到 {result['retrieved_count']} 个相关片段",
                "retrieved_count": result["retrieved_count"],
            },
        }
        yield {
            "type": "retrieval",
            "data": {
                "stage": "rerank",
                "message": f"已选取 {result['reranked_count']} 个高相关片段",
                "retrieved_count": result["retrieved_count"],
                "reranked_count": result["reranked_count"],
            },
        }
        hits = select_evidence(result["ranked"])
        confidence_result = evaluate_confidence(hits)
        confidence = confidence_result.level
        stage_started = time.perf_counter()
        citations = await _persist_citations(assistant_message_id, hits)
        citation_persistence_ms = round((time.perf_counter() - stage_started) * 1000)
        yield {"type": "citations", "data": {"items": citations}}

        if not hits:
            answer = "根据当前知识库，未找到足够信息回答该问题，建议联系知识库管理员。"
            input_tokens = output_tokens = 0
            llm_first_token_ms = llm_total_ms = 0
            yield {"type": "token", "data": {"content": answer}}
        else:
            if not settings.DASHSCOPE_API_KEY:
                raise RuntimeError("未配置 DASHSCOPE_API_KEY")
            prompt = RAGPrompts.rag_prompt().format(
                context=_context(hits),
                history="\n".join(
                    f"Q: {item['question']}\nA: {item['answer']}" for item in history
                )
                or "（无）",
                question=source["query"],
            )
            client = AsyncOpenAI(
                api_key=settings.DASHSCOPE_API_KEY,
                base_url=settings.DASHSCOPE_BASE_URL,
                timeout=settings.RAG_LLM_TIMEOUT_SECONDS,
            )
            llm_started = time.perf_counter()
            parts = []
            input_tokens = output_tokens = 0
            llm_first_token_ms = 0
            async with _llm_slots:
                stream = await client.chat.completions.create(
                    model=settings.LLM_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": "你是企业内部技术知识库智能助手。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.1,
                    stream=True,
                    stream_options={"include_usage": True},
                )
                async for chunk in stream:
                    if chunk.usage:
                        input_tokens = chunk.usage.prompt_tokens
                        output_tokens = chunk.usage.completion_tokens
                    token = (
                        chunk.choices[0].delta.content or ""
                        if chunk.choices
                        else ""
                    )
                    if token:
                        if not parts:
                            llm_first_token_ms = round(
                                (time.perf_counter() - llm_started) * 1000
                            )
                        parts.append(token)
                        yield {"type": "token", "data": {"content": token}}
                        if len(parts) % 20 == 0:
                            await db.execute(
                                "UPDATE chat_messages SET content=$2 WHERE id=$1",
                                (assistant_message_id, "".join(parts)),
                            )
            answer = "".join(parts)
            llm_total_ms = round((time.perf_counter() - llm_started) * 1000)

        timings = {
            **result["timings"],
            "retrieval_ms": result["latency_ms"],
            "citation_persistence_ms": citation_persistence_ms,
            "llm_first_token_ms": llm_first_token_ms,
            "llm_total_ms": llm_total_ms,
            "answer_total_ms": round((time.perf_counter() - answer_started) * 1000),
        }
        usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "retrieval_run_id": str(result["retrieval_run_id"]),
            "latency_ms": result["latency_ms"],
            "confidence_score": confidence_result.score,
            "confidence_reason": confidence_result.reason,
            "evidence_count": confidence_result.evidence_count,
            "timings": timings,
        }
        await db.execute(
            """UPDATE chat_messages SET content=$2,status='completed',confidence=$3,
                      citation_count=$4,usage=$5::jsonb,completed_at=now(),error_code=NULL
               WHERE id=$1""",
            (
                assistant_message_id,
                answer,
                confidence,
                len(citations),
                json.dumps(usage),
            ),
        )
        logger.info(
            "RAG answer completed",
            extra={
                "event": "rag.answer.completed",
                "confidence": confidence,
                "citation_count": len(citations),
            },
        )
        await db.execute(
            """INSERT INTO audit_logs
               (organization_id,user_id,action,resource_type,resource_id,metadata)
               VALUES ($1,$2,'rag.answer_completed','chat_message',$3,$4::jsonb)""",
            (
                current_user["organization_id"],
                current_user["id"],
                str(assistant_message_id),
                json.dumps(
                    {
                        "confidence": confidence,
                        "confidence_score": confidence_result.score,
                        "confidence_reason": confidence_result.reason,
                        "citation_count": len(citations),
                        "evidence_count": confidence_result.evidence_count,
                    }
                ),
            ),
        )
        yield {
            "type": "final",
            "data": {
                "message_id": assistant_message_id,
                "confidence": confidence,
                "citation_count": len(citations),
                "usage": usage,
            },
        }
    except Exception as exc:
        logger.exception(
            "RAG answer failed",
            extra={"event": "rag.answer.failed"},
        )
        await db.execute(
            """UPDATE chat_messages SET status='failed',error_code='RAG_FAILED',
                      content=CASE WHEN content='' THEN $2 ELSE content END,
                      completed_at=now() WHERE id=$1""",
            (assistant_message_id, str(exc)[:1000]),
        )
        yield {
            "type": "error",
            "data": {"code": "RAG_FAILED", "message": str(exc)[:300]},
        }


async def mark_stream_interrupted(message_id: UUID) -> None:
    await db.execute(
        """UPDATE chat_messages SET status='failed',error_code='STREAM_DISCONNECTED',
                  completed_at=now() WHERE id=$1 AND status='running'""",
        (message_id,),
    )


async def message_citations(message_id: UUID, current_user: dict) -> dict:
    message = await get_message(message_id, current_user)
    items = await _citation_items(message_id, current_user)
    return {
        "answer_basis_count": len(items),
        "confidence": message["confidence"] or "low",
        "items": items,
    }


async def citation_detail(citation_id: UUID, current_user: dict) -> dict:
    row = await db.fetch_one(
        f"""SELECT c.id, d.id AS document_id, d.name AS document_name,
                  ch.location_label, c.excerpt, ch.content,
                  ARRAY[]::text[] AS highlights, true AS access_allowed
           FROM message_citations c
           JOIN chat_messages m ON m.id=c.message_id
           JOIN chat_sessions s ON s.id=m.session_id
           JOIN document_chunks ch ON ch.id=c.chunk_id
           JOIN documents d ON d.id=ch.document_id
           JOIN knowledge_bases kb ON kb.id=d.knowledge_base_id
           JOIN knowledge_base_members member ON member.knowledge_base_id=kb.id
           WHERE c.id=$1 AND s.organization_id=$2 AND s.user_id=$3
             AND member.user_id=$3 AND d.deleted_at IS NULL
             AND {document_access_sql("d", "$4", "$5")}""",
        (
            citation_id,
            current_user["organization_id"],
            current_user["id"],
            current_user.get("security_level", "public"),
            current_user.get("team"),
        ),
    )
    if not row:
        raise _error(404, "CITATION_NOT_FOUND", "引用不存在或无权访问")
    return row


def retrieval_payload(result: dict) -> dict:
    def item(hit: Hit, stage: str, rank: int) -> dict:
        return {
            "chunk_id": hit.metadata["chunk_id"],
            "document_id": hit.metadata["document_id"],
            "document_name": hit.metadata["source"],
            "location_label": hit.metadata["location_label"],
            "stage": stage,
            "rank": rank,
            "score": hit.score,
            "excerpt": hit.text[:500],
        }

    return {
        "retrieval_run_id": result["retrieval_run_id"],
        "query": result["query"],
        "rewritten_query": result["rewritten_query"],
        "retrieved_count": result["retrieved_count"],
        "reranked_count": result["reranked_count"],
        "latency_ms": result["latency_ms"],
        "timings": result["timings"],
        "results": [
            *[
                item(hit, "retrieve", rank)
                for rank, hit in enumerate(result["retrieved"], 1)
            ],
            *[
                item(hit, "rerank", rank)
                for rank, hit in enumerate(result["ranked"], 1)
            ],
        ],
    }
