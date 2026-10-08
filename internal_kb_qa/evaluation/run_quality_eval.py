"""Run the live retrieval baseline against the local DevMind AI stack."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DATASET = Path(__file__).with_name("rag_quality_set.json")
DEFAULT_OUTPUT = ROOT / "docs/eval/rag_quality_baseline.json"
SEED_FILES = (
    "tech.md",
    "access_request.md",
    "incident.md",
    "policy_general.md",
    "ticket_inquiry.md",
    "complaint_suggestion.md",
)


def _data(response: httpx.Response):
    if response.is_error:
        raise RuntimeError(f"{response.request.method} {response.url}: {response.status_code} {response.text}")
    body = response.json()
    if body.get("error"):
        raise RuntimeError(json.dumps(body["error"], ensure_ascii=False))
    return body["data"]


def _relevant(row: dict, expected: set[str], terms: list[str]) -> bool:
    if row["document_name"] not in expected:
        return False
    text = row.get("content", row.get("excerpt", "")).casefold()
    return not terms or any(term.casefold() in text for term in terms)


def _relevance(rows: list[dict], expected: set[str], terms: list[str], k: int) -> list[int]:
    return [int(_relevant(row, expected, terms)) for row in rows[:k]]


def _mrr(rows: list[dict], expected: set[str], terms: list[str], k: int) -> float:
    return next((1 / rank for rank, row in enumerate(rows[:k], 1) if _relevant(row, expected, terms)), 0.0)


def _recall(rows: list[dict], expected: set[str], terms: list[str], k: int) -> float:
    if not expected:
        return 0.0
    found = {row["document_name"] for row in rows[:k] if _relevant(row, expected, terms)}
    return len(found) / len(expected)


def _ndcg(rows: list[dict], expected: set[str], terms: list[str], k: int) -> float:
    gains = _relevance(rows, expected, terms, k)
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, 1))
    relevant_chunks = sum(_relevant(row, expected, terms) for row in rows)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(relevant_chunks, k) + 1))
    return dcg / ideal if ideal else 0.0


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


async def _auth(client: httpx.AsyncClient, api: str) -> dict[str, str]:
    username = os.getenv("RAG_EVAL_USER", "rag_eval_runner")
    password = os.getenv("RAG_EVAL_PASSWORD", "DevMind-eval-local-2026!")
    login = await client.post(f"{api}/auth/login", json={"account": username, "password": password})
    if login.status_code == 200:
        token = _data(login)["access_token"]
        return {"Authorization": f"Bearer {token}"}
    phone = os.getenv("RAG_EVAL_PHONE", "13900009998")
    code = _data(await client.post(f"{api}/auth/send-code", json={"phone": phone}))["debug_code"]
    auth = _data(
        await client.post(
            f"{api}/auth/register",
            json={
                "username": username,
                "password": password,
                "nickname": "RAG 质量评测",
                "phone": phone,
                "sms_code": code,
                "team": "quality",
            },
        )
    )
    return {"Authorization": f"Bearer {auth['access_token']}"}


async def _prepare_corpus(client: httpx.AsyncClient, api: str, headers: dict[str, str]) -> str:
    listed = _data(await client.get(f"{api}/knowledge-bases", headers=headers, params={"keyword": "RAG 质量评测基准", "limit": 100}))
    kb = next((item for item in listed["items"] if item["name"] == "RAG 质量评测基准"), None)
    if not kb:
        kb = _data(await client.post(f"{api}/knowledge-bases", headers=headers, json={"name": "RAG 质量评测基准", "description": "40 条 Pilot 质量评测专用语料"}))
    kb_id = str(kb["id"])
    docs = _data(await client.get(f"{api}/documents", headers=headers, params={"page_size": 100}))
    existing = {item["name"]: item for item in docs["items"] if str(item["knowledge_base_id"]) == kb_id and item["status"] != "deleted"}

    pending: list[str] = []
    for filename in SEED_FILES:
        if filename in existing and existing[filename]["status"] == "ready":
            continue
        if filename in existing:
            pending.append(str(existing[filename]["id"]))
            continue
        path = ROOT / "internal_kb_qa/data/rag_seed" / filename
        with path.open("rb") as file_handle:
            uploaded = _data(
                await client.post(
                    f"{api}/documents",
                    headers=headers,
                    files={"file": (filename, file_handle, "text/markdown")},
                    data={"knowledge_base_id": kb_id, "display_name": filename},
                )
            )
        pending.append(str(uploaded["id"]))

    deadline = time.monotonic() + 1200
    while pending:
        for document_id in pending.copy():
            task = _data(await client.get(f"{api}/documents/{document_id}/task", headers=headers))
            if task["status"] == "completed":
                pending.remove(document_id)
            elif task["status"] == "failed":
                raise RuntimeError(f"document {document_id} failed: {task}")
        if pending:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"document indexing timeout: {pending}")
            await asyncio.sleep(2)
    return kb_id


async def _chunk_contents(client: httpx.AsyncClient, api: str, headers: dict[str, str], kb_id: str) -> dict[str, str]:
    docs = _data(await client.get(f"{api}/documents", headers=headers, params={"page_size": 100}))
    contents: dict[str, str] = {}
    for document in docs["items"]:
        if str(document["knowledge_base_id"]) != kb_id or document["name"] not in SEED_FILES:
            continue
        chunks = _data(await client.get(f"{api}/documents/{document['id']}/chunks", headers=headers, params={"limit": 100}))
        contents.update({str(chunk["id"]): chunk["content"] for chunk in chunks["items"]})
    return contents


def _summarize(cases: list[dict]) -> dict:
    answerable = [case for case in cases if case["answerable"]]
    unanswered = [case for case in cases if not case["answerable"]]
    return {
        "case_count": len(cases),
        "answerable_count": len(answerable),
        "unanswerable_count": len(unanswered),
        "retrieval": {
            "mrr_at_10": _mean([case["metrics"]["mrr_at_10"] for case in answerable]),
            "recall_at_5": _mean([case["metrics"]["recall_at_5"] for case in answerable]),
            "recall_at_10": _mean([case["metrics"]["recall_at_10"] for case in answerable]),
            "ndcg_at_5": _mean([case["metrics"]["ndcg_at_5"] for case in answerable]),
            "precision_at_3": _mean([case["metrics"]["precision_at_3"] for case in answerable]),
            "rerank_top1_accuracy": _mean([case["metrics"]["rerank_top1_relevant"] for case in answerable]),
        },
        "confidence": {
            "current_false_high_rate_on_unanswerable": _mean([int(case["current_confidence"] == "high") for case in unanswered]),
            "current_false_non_low_rate_on_unanswerable": _mean([int(case["current_confidence"] != "low") for case in unanswered]),
        },
        "latency_ms": {
            "mean": _mean([case["latency_ms"] for case in cases]),
            "max": max((case["latency_ms"] for case in cases), default=0),
        },
    }


def _recompute(report: dict) -> dict:
    for case in report["cases"]:
        expected = set(case["expected_sources"])
        terms = case["expected_terms"]
        retrieved, reranked = case["retrieved"], case["reranked"]
        case["metrics"] = {
            "mrr_at_10": _mrr(retrieved, expected, terms, 10),
            "recall_at_5": _recall(retrieved, expected, terms, 5),
            "recall_at_10": _recall(retrieved, expected, terms, 10),
            "ndcg_at_5": _ndcg(retrieved, expected, terms, 5),
            "precision_at_3": _mean(_relevance(retrieved, expected, terms, 3)),
            "rerank_top1_relevant": int(bool(reranked) and _relevant(reranked[0], expected, terms)),
        }
    report["summary"] = _summarize(report["cases"])
    return report


async def run(
    api: str, output: Path, top_k: int = 10, rerank_top_n: int = 5
) -> dict:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    async with httpx.AsyncClient(timeout=600) as client:
        headers = await _auth(client, api)
        kb_id = await _prepare_corpus(client, api, headers)
        chunk_contents = await _chunk_contents(client, api, headers, kb_id)
        cases = []
        for index, item in enumerate(dataset["items"], 1):
            result = _data(
                await client.post(
                    f"{api}/retrieval/test",
                    headers=headers,
                    json={
                        "query": item["query"],
                        "knowledge_base_ids": [kb_id],
                        "top_k": top_k,
                        "rerank_top_n": rerank_top_n,
                    },
                )
            )
            for row in result["results"]:
                row["content"] = chunk_contents.get(str(row["chunk_id"]), row["excerpt"])
            retrieved = [row for row in result["results"] if row["stage"] == "retrieve"]
            reranked = [row for row in result["results"] if row["stage"] == "rerank"]
            expected = set(item["expected_sources"])
            terms = item["expected_terms"]
            scores = [float(row["score"]) for row in reranked]
            current_score = _mean(scores)
            case = {
                **item,
                "latency_ms": result["latency_ms"],
                "timings": result.get("timings", {}),
                "rewritten_query": result["rewritten_query"],
                "retrieved": retrieved,
                "reranked": reranked,
                "current_score": current_score,
                "current_confidence": "high" if current_score >= 0.75 else "medium" if current_score >= 0.5 else "low",
                "metrics": {
                    "mrr_at_10": _mrr(retrieved, expected, terms, 10),
                    "recall_at_5": _recall(retrieved, expected, terms, 5),
                    "recall_at_10": _recall(retrieved, expected, terms, 10),
                    "ndcg_at_5": _ndcg(retrieved, expected, terms, 5),
                    "precision_at_3": _mean(_relevance(retrieved, expected, terms, 3)),
                    "rerank_top1_relevant": int(bool(reranked) and _relevant(reranked[0], expected, terms)),
                },
            }
            cases.append(case)
            print(f"[{index:02d}/{len(dataset['items'])}] {item['id']} top1={scores[0] if scores else 0:.4f} current={case['current_confidence']}", flush=True)
        report = {"version": dataset["version"], "knowledge_base_id": kb_id, "summary": _summarize(cases), "cases": cases}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


async def reuse(api: str, output: Path) -> dict:
    report = json.loads(output.read_text(encoding="utf-8"))
    async with httpx.AsyncClient(timeout=300) as client:
        headers = await _auth(client, api)
        contents = await _chunk_contents(client, api, headers, str(report["knowledge_base_id"]))
    for case in report["cases"]:
        for row in [*case["retrieved"], *case["reranked"]]:
            row["content"] = contents.get(str(row["chunk_id"]), row.get("excerpt", ""))
    report = _recompute(report)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:15200/api/v1")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--reuse", action="store_true", help="只从已有原始结果重新计算指标")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--rerank-top-n", type=int, default=5)
    args = parser.parse_args()
    if args.reuse:
        result = asyncio.run(reuse(args.api, args.output))
    else:
        result = asyncio.run(
            run(args.api, args.output, args.top_k, args.rerank_top_n)
        )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
