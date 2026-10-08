"""Against running services: uv run python tests/live_e2e.py."""

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import websockets

BACKEND = os.getenv("DEVMIND_E2E_BACKEND", "http://127.0.0.1:15200")
API = f"{BACKEND}/api/v1"
WEB = os.getenv("DEVMIND_E2E_WEB", "http://127.0.0.1:5173")
ROOT = Path(__file__).resolve().parents[1]


def payload(response: httpx.Response):
    if response.is_error:
        raise RuntimeError(f"{response.request.method} {response.url}: {response.status_code} {response.text}")
    body = response.json()
    if body.get("error"):
        raise RuntimeError(json.dumps(body["error"], ensure_ascii=False))
    return body["data"]


async def main() -> None:
    stamp = str(int(time.time()))
    username, phone = f"e2e_{stamp}", f"139{stamp[-8:]}"
    summary = {"user": username}

    async with httpx.AsyncClient(timeout=300) as client:
        for route in ("/", "/login", "/workspace"):
            page = await client.get(f"{WEB}{route}")
            assert page.status_code == 200 and "DevMind AI" in page.text

        ready = (await client.get(f"{BACKEND}/health/ready")).json()
        assert ready["status"] == "ready"

        code = payload(await client.post(f"{API}/auth/send-code", json={"phone": phone}))["debug_code"]
        auth = payload(
            await client.post(
                f"{API}/auth/register",
                json={
                    "username": username,
                    "password": "DevMind-e2e-2026!",
                    "nickname": "E2E 联调用户",
                    "phone": phone,
                    "sms_code": code,
                    "team": "e2e",
                },
            )
        )
        headers = {"Authorization": f"Bearer {auth['access_token']}"}
        me = payload(await client.get(f"{API}/me", headers=headers))
        assert me["username"] == username

        kb = payload(
            await client.post(
                f"{API}/knowledge-bases",
                headers=headers,
                json={"name": f"E2E 技术知识库 {stamp}", "description": "真实全链路联调"},
            )
        )
        kb_id = str(kb["id"])
        summary["knowledge_base_id"] = kb_id
        members = payload(await client.get(f"{API}/knowledge-bases/{kb_id}/members", headers=headers))
        assert members["items"][0]["role"] == "admin"

        preferences = payload(
            await client.patch(
                f"{API}/me/preferences",
                headers=headers,
                json={
                    "default_knowledge_base_ids": [kb_id],
                    "default_mode": "tech",
                    "answer_style": "concise",
                    "locale": "zh-CN",
                    "timezone": "Asia/Shanghai",
                },
            )
        )
        assert preferences["default_knowledge_base_ids"] == [kb_id]

        source = ROOT / "internal_kb_qa/data/rag_seed/tech.md"
        with source.open("rb") as file_handle:
            uploaded = payload(
                await client.post(
                    f"{API}/documents",
                    headers=headers,
                    files={"file": (source.name, file_handle, "text/markdown")},
                    data={"knowledge_base_id": kb_id, "display_name": f"E2E 技术手册 {stamp}.md"},
                )
            )
        document_id = str(uploaded["id"])
        summary["document_id"] = document_id

        deadline = time.monotonic() + 900
        while True:
            task = payload(await client.get(f"{API}/documents/{document_id}/task", headers=headers))
            if task["status"] in {"completed", "failed"}:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(f"document task timeout: {task}")
            await asyncio.sleep(2)
        assert task["status"] == "completed", task
        document = payload(await client.get(f"{API}/documents/{document_id}", headers=headers))
        assert document["status"] == "ready" and document["chunk_count"] > 0
        chunks = payload(await client.get(f"{API}/documents/{document_id}/chunks", headers=headers))
        assert chunks["items"]
        summary["chunk_count"] = document["chunk_count"]

        query = "接口返回 503 Service Unavailable 时应该如何排查？"
        retrieval = payload(
            await client.post(
                f"{API}/retrieval/test",
                headers=headers,
                json={"query": query, "knowledge_base_ids": [kb_id], "top_k": 10, "rerank_top_n": 3},
            )
        )
        assert retrieval["retrieved_count"] > 0 and retrieval["reranked_count"] > 0
        summary["retrieved_count"] = retrieval["retrieved_count"]

        session = payload(
            await client.post(
                f"{API}/sessions",
                headers=headers,
                json={"title": "E2E 联调会话", "knowledge_base_ids": [kb_id]},
            )
        )
        session_id = str(session["id"])
        created = payload(
            await client.post(
                f"{API}/messages",
                headers=headers,
                json={"session_id": session_id, "content": query, "mode": "tech", "knowledge_base_ids": [kb_id]},
            )
        )
        assistant_id = str(created["assistant_message_id"])
        events, answer = [], []
        socket_base = BACKEND.replace("http://", "ws://").replace("https://", "wss://")
        async with websockets.connect(f"{socket_base}{created['stream_url']}", open_timeout=30) as socket:
            while True:
                event = json.loads(await asyncio.wait_for(socket.recv(), timeout=300))
                events.append(event["type"])
                if event["type"] == "token":
                    answer.append(event["data"]["content"])
                if event["type"] == "error":
                    raise RuntimeError(json.dumps(event["data"], ensure_ascii=False))
                if event["type"] == "final":
                    summary["confidence"] = event["data"]["confidence"]
                    summary["citation_count"] = event["data"]["citation_count"]
                    break
        assert answer and {"status", "retrieval", "citations", "token", "final"} <= set(events)

        citations = payload(await client.get(f"{API}/messages/{assistant_id}/citations", headers=headers))
        assert citations["items"]
        citation = payload(await client.get(f"{API}/citations/{citations['items'][0]['id']}", headers=headers))
        assert citation["access_allowed"] is True and citation["content"]

        payload(await client.put(f"{API}/favorites/messages/{assistant_id}", headers=headers))
        favorites = payload(await client.get(f"{API}/favorites", headers=headers))
        assert any(str(item["message_id"]) == assistant_id for item in favorites["items"])
        payload(await client.delete(f"{API}/favorites/messages/{assistant_id}", headers=headers))

        renamed = payload(
            await client.patch(
                f"{API}/sessions/{session_id}", headers=headers, json={"title": "E2E 已验证会话"}
            )
        )
        assert renamed["title"] == "E2E 已验证会话"
        history = payload(await client.get(f"{API}/sessions", headers=headers, params={"keyword": "已验证"}))
        assert any(str(item["id"]) == session_id for item in history["items"])
        detail = payload(await client.get(f"{API}/sessions/{session_id}", headers=headers))
        assert len(detail["messages"]) == 2 and detail["messages"][1]["status"] == "completed"

        payload(await client.post(f"{API}/auth/logout", headers=headers))
        assert (await client.get(f"{API}/me", headers=headers)).status_code == 401

    summary["answer_chars"] = len("".join(answer))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
