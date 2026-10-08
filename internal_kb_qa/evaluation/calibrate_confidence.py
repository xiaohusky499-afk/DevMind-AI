"""Compare confidence formulas using saved live retrieval results."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "docs/eval/rag_quality_baseline.json"
OUTPUT = ROOT / "docs/eval/confidence_calibration.json"


def _level(score: float, high: float, medium: float) -> str:
    return "high" if score >= high else "medium" if score >= medium else "low"


def _scores(values: list[float]) -> dict[str, float]:
    padded = (values + [0.0, 0.0, 0.0])[:3]
    top1, top2, top3 = padded
    return {
        "current_mean": sum(values) / len(values) if values else 0.0,
        "top1": top1,
        "composite": min(1.0, 0.75 * top1 + 0.15 * top2 + 0.10 * top3 + 0.05 * max(0.0, top1 - top2)),
    }


def main(baseline_path: Path = BASELINE, output: Path = OUTPUT) -> None:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    configs = {
        "current_mean": {"high": 0.75, "medium": 0.50},
        "top1": {"high": 0.80, "medium": 0.12},
        "composite": {"high": 0.68, "medium": 0.09},
    }
    results = {}
    for name, thresholds in configs.items():
        rows = []
        for case in baseline["cases"]:
            values = [float(item["score"]) for item in case["reranked"]]
            score = _scores(values)[name]
            rows.append({
                "id": case["id"],
                "answerable": case["answerable"],
                "top1_relevant": bool(case["metrics"]["rerank_top1_relevant"]),
                "score": score,
                "level": _level(score, thresholds["high"], thresholds["medium"]),
            })
        high = [row for row in rows if row["level"] == "high"]
        answerable = [row for row in rows if row["answerable"]]
        unanswerable = [row for row in rows if not row["answerable"]]
        results[name] = {
            "thresholds": thresholds,
            "distribution": dict(Counter(row["level"] for row in rows)),
            "high_precision": sum(row["answerable"] and row["top1_relevant"] for row in high) / len(high) if high else 0.0,
            "answerable_non_low_rate": sum(row["level"] != "low" for row in answerable) / len(answerable),
            "unanswerable_low_rate": sum(row["level"] == "low" for row in unanswerable) / len(unanswerable),
            "answerable_low_ids": [row["id"] for row in answerable if row["level"] == "low"],
        }
    chosen = results["composite"]
    assert chosen["high_precision"] >= 0.90
    assert chosen["answerable_non_low_rate"] >= 0.90
    assert chosen["unanswerable_low_rate"] >= 0.90
    report = {"selected": "composite", "formula": "0.75*top1 + 0.15*top2 + 0.10*top3 + 0.05*(top1-top2)", "candidates": results}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=BASELINE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    main(args.input, args.output)
