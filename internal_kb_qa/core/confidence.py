"""Calibrated evidence selection and answer confidence."""

from __future__ import annotations

from dataclasses import dataclass

from .hit import Hit

MIN_EVIDENCE_SCORE = 0.12
RELATIVE_EVIDENCE_SCORE = 0.45
HIGH_CONFIDENCE_SCORE = 0.68
MEDIUM_CONFIDENCE_SCORE = 0.09
MAX_EVIDENCE_COUNT = 5


@dataclass(frozen=True)
class ConfidenceResult:
    score: float
    level: str
    reason: str
    evidence_count: int


def select_evidence(hits: list[Hit]) -> list[Hit]:
    ranked = sorted(hits, key=lambda hit: hit.score, reverse=True)
    if not ranked or ranked[0].score < MIN_EVIDENCE_SCORE:
        return []
    relative_floor = ranked[0].score * RELATIVE_EVIDENCE_SCORE
    return [
        hit
        for hit in ranked[:MAX_EVIDENCE_COUNT]
        if hit.score >= MIN_EVIDENCE_SCORE and hit.score >= relative_floor
    ]


def evaluate_confidence(hits: list[Hit]) -> ConfidenceResult:
    evidence = select_evidence(hits)
    if not evidence:
        return ConfidenceResult(0.0, "low", "insufficient_evidence", 0)
    scores = [hit.score for hit in evidence] + [0.0, 0.0, 0.0]
    top1, top2, top3 = scores[:3]
    score = min(
        1.0,
        0.75 * top1
        + 0.15 * top2
        + 0.10 * top3
        + 0.05 * max(0.0, top1 - top2),
    )
    level = (
        "high"
        if score >= HIGH_CONFIDENCE_SCORE
        else "medium"
        if score >= MEDIUM_CONFIDENCE_SCORE
        else "low"
    )
    reason = "strong_evidence" if level == "high" else "limited_evidence"
    return ConfidenceResult(round(score, 6), level, reason, len(evidence))
