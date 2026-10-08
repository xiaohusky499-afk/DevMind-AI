"""Progressive live load test for the formal RAG WebSocket path."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
import psutil
import websockets

from internal_kb_qa.evaluation.run_quality_eval import (
    DATASET,
    DEFAULT_OUTPUT,
    _auth,
    _data,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOAD_OUTPUT = ROOT / "docs/eval/rag_load_test.json"


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.999) - 1))]


async def _sample_process(process: psutil.Process, done: asyncio.Event) -> dict:
    cpu, memory = [], []
    process.cpu_percent(None)
    while not done.is_set():
        try:
            cpu.append(process.cpu_percent(None))
            memory.append(process.memory_info().rss / 1024 / 1024)
        except psutil.Error:
            break
        try:
            await asyncio.wait_for(done.wait(), timeout=0.5)
        except TimeoutError:
            pass
    return {
        "cpu_percent_max": max(cpu, default=0.0),
        "memory_mb_max": max(memory, default=0.0),
    }


async def _request(
    client: httpx.AsyncClient,
    api: str,
    headers: dict[str, str],
    kb_id: str,
    item: dict,
) -> dict:
    started = time.perf_counter()
    first_token_ms = 0
    try:
        session = _data(
            await client.post(
                f"{api}/sessions",
                headers=headers,
                json={"title": f"压测 {item['id']}", "knowledge_base_ids": [kb_id]},
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
        final = {}
        async with websockets.connect(
            f"ws://127.0.0.1:15200{created['stream_url']}", open_timeout=30
        ) as socket:
            while True:
                event = json.loads(await asyncio.wait_for(socket.recv(), timeout=900))
                if event["type"] == "token" and not first_token_ms:
                    first_token_ms = round((time.perf_counter() - started) * 1000)
                elif event["type"] == "error":
                    raise RuntimeError(event["data"]["message"])
                elif event["type"] == "final":
                    final = event["data"]
                    break
        return {
            "id": item["id"],
            "ok": True,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "first_token_ms": first_token_ms,
            "usage": final.get("usage") or {},
        }
    except Exception as exc:  # noqa: BLE001 -- load test records each request failure
        return {
            "id": item["id"],
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "first_token_ms": first_token_ms,
            "error": str(exc)[:500],
        }


async def run(
    api: str,
    output: Path,
    levels: list[int],
    server_pid: int | None,
    max_p95_ms: int,
) -> dict:
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    baseline = json.loads(DEFAULT_OUTPUT.read_text(encoding="utf-8"))
    kb_id = str(baseline["knowledge_base_id"])
    items = [item for item in dataset["items"] if item["answerable"]]
    report = {"knowledge_base_id": kb_id, "levels": []}
    async with httpx.AsyncClient(timeout=900) as client:
        headers = await _auth(client, api)
        for concurrency in levels:
            batch = [items[index % len(items)] for index in range(concurrency)]
            done = asyncio.Event()
            sampler = (
                asyncio.create_task(_sample_process(psutil.Process(server_pid), done))
                if server_pid
                else None
            )
            started = time.perf_counter()
            requests = await asyncio.gather(
                *[_request(client, api, headers, kb_id, item) for item in batch]
            )
            wall_seconds = time.perf_counter() - started
            done.set()
            resources = await sampler if sampler else {}
            successful = [item for item in requests if item["ok"]]
            latencies = [item["latency_ms"] for item in successful]
            first_tokens = [item["first_token_ms"] for item in successful]
            result = {
                "concurrency": concurrency,
                "success_count": len(successful),
                "error_count": concurrency - len(successful),
                "error_rate": (concurrency - len(successful)) / concurrency,
                "throughput_rps": len(successful) / wall_seconds,
                "wall_seconds": wall_seconds,
                "latency_ms": {
                    "p50": _percentile(latencies, 0.50),
                    "p95": _percentile(latencies, 0.95),
                    "max": max(latencies, default=0),
                },
                "first_token_ms": {
                    "p50": _percentile(first_tokens, 0.50),
                    "p95": _percentile(first_tokens, 0.95),
                },
                "resources": resources,
                "requests": requests,
            }
            report["levels"].append(result)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(
                f"concurrency={concurrency} success={len(successful)}/{concurrency} "
                f"p95={result['latency_ms']['p95']:.0f}ms "
                f"throughput={result['throughput_rps']:.3f}rps",
                flush=True,
            )
            if (
                result["error_rate"] > 0.05
                or result["latency_ms"]["p95"] > max_p95_ms
            ):
                print("stop: service reached the configured saturation boundary", flush=True)
                break
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:15200/api/v1")
    parser.add_argument("--output", type=Path, default=DEFAULT_LOAD_OUTPUT)
    parser.add_argument("--levels", default="1,10,20,50")
    parser.add_argument("--server-pid", type=int)
    parser.add_argument("--max-p95-ms", type=int, default=120_000)
    args = parser.parse_args()
    asyncio.run(
        run(
            args.api,
            args.output,
            [int(value) for value in args.levels.split(",") if value.strip()],
            args.server_pid,
            args.max_p95_ms,
        )
    )
