"""Run live answer, citation, refusal, and calibrated-confidence evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import websockets

from internal_kb_qa.evaluation.run_quality_eval import (
    DATASET,
    DEFAULT_OUTPUT,
    _auth,
    _data,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANSWER_OUTPUT = ROOT / "docs/eval/rag_quality_final.json"
REFUSAL_MARKERS = ("未找到足够信息", "未找到足够置信度", "不在知识库服务范围")


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _term_coverage(answer: str, terms: list[str]) -> float:
    folded = answer.casefold()
    return _mean([float(term.casefold() in folded) for term in terms]) if terms else 1.0


def _summary(cases: list[dict]) -> dict:
    answerable = [case for case in cases if case["answerable"]]
    unanswerable = [case for case in cases if not case["answerable"]]
    answered = [case for case in answerable if not case["refused"]]
    high = [case for case in cases if case["confidence"] == "high"]
    high_correct = [
        case
        for case in high
        if case["answerable"]
        and not case["refused"]
        and case["citation_source_precision"] > 0
        and case["term_coverage"] >= 0.5
    ]
    return {
        "case_count": len(cases),
        "answerable_count": len(answerable),
        "unanswerable_count": len(unanswerable),
        "answerable_answer_rate": _mean([float(not case["refused"]) for case in answerable]),
        "unanswerable_refusal_rate": _mean([float(case["refused"]) for case in unanswerable]),
        "citation_trace_rate": _mean([float(bool(case["citations"])) for case in answered]),
        "citation_source_precision": _mean([case["citation_source_precision"] for case in answered]),
        "answer_term_coverage": _mean([case["term_coverage"] for case in answered]),
        "high_confidence_precision": len(high_correct) / len(high) if high else 0.0,
        "confidence_distribution": {
            level: sum(case["confidence"] == level for case in cases)
            for level in ("high", "medium", "low")
        },
        "latency_ms": {
            "mean": _mean([case["latency_ms"] for case in cases]),
            "max": max((case["latency_ms"] for case in cases), default=0),
        },
    }


async def _answer(
    client: httpx.AsyncClient,
    api: str,
    headers: dict[str, str],
    kb_id: str,
    item: dict,
) -> dict:
    session = _data(
        await client.post(
            f"{api}/sessions",
            headers=headers,
            json={"title": f"评测 {item['id']}", "knowledge_base_ids": [kb_id]},
        )
    )
    created = _data(
        await client.post(
            f"{api}/messages",
            headers=headers,
            json={
                "session_id": str(session["id"]),
                "content": item["query"],
                "mode": "tech",
                "knowledge_base_ids": [kb_id],
            },
        )
    )
    started = time.monotonic()
    answer_parts: list[str] = []
    citations: list[dict] = []
    final: dict = {}
    websocket_base = os.getenv("RAG_EVAL_WS", "ws://127.0.0.1:15200")
    async with websockets.connect(
        f"{websocket_base}{created['stream_url']}", open_timeout=30
    ) as socket:
        while True:
            event = json.loads(await asyncio.wait_for(socket.recv(), timeout=600))
            if event["type"] == "token":
                answer_parts.append(event["data"]["content"])
            elif event["type"] == "citations":
                citations = event["data"]["items"]
            elif event["type"] == "error":
                raise RuntimeError(json.dumps(event["data"], ensure_ascii=False))
            elif event["type"] == "final":
                final = event["data"]
                break
    answer = "".join(answer_parts)
    expected = set(item["expected_sources"])
    precision = _mean(
        [float(citation["document_name"] in expected) for citation in citations]
    )
    refused = any(marker in answer for marker in REFUSAL_MARKERS)
    return {
        **item,
        "session_id": str(session["id"]),
        "assistant_message_id": str(created["assistant_message_id"]),
        "answer": answer,
        "refused": refused,
        "confidence": final.get("confidence", "low"),
        "usage": final.get("usage") or {},
        "citations": citations,
        "citation_source_precision": precision,
        "term_coverage": _term_coverage(answer, item["expected_terms"]) if not refused else 0.0,
        "latency_ms": round((time.monotonic() - started) * 1000),
    }


async def run(api: str, output: Path, limit: int | None, resume: bool) -> dict:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    baseline = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    kb_id = str(baseline["knowledge_base_id"])
    cases: list[dict] = []
    if resume and output.exists():
        cases = json.loads(output.read_text(encoding="utf-8")).get("cases", [])
    completed = {case["id"] for case in cases}
    items = dataset["items"][:limit] if limit else dataset["items"]
    async with httpx.AsyncClient(timeout=600) as client:
        headers = await _auth(client, api)
        for index, item in enumerate(items, 1):
            if item["id"] in completed:
                continue
            case = await _answer(client, api, headers, kb_id, item)
            cases.append(case)
            report = {
                "version": dataset["version"],
                "knowledge_base_id": kb_id,
                "summary": _summary(cases),
                "cases": cases,
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                f"[{index:02d}/{len(items)}] {item['id']} "
                f"confidence={case['confidence']} citations={len(case['citations'])} "
                f"refused={case['refused']} coverage={case['term_coverage']:.2f}",
                flush=True,
            )
    return {
        "version": dataset["version"],
        "knowledge_base_id": kb_id,
        "summary": _summary(cases),
        "cases": cases,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:15200/api/v1")
    parser.add_argument("--output", type=Path, default=DEFAULT_ANSWER_OUTPUT)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(run(args.api, args.output, args.limit, args.resume))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
