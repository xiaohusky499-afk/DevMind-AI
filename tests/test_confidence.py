from internal_kb_qa.core.confidence import evaluate_confidence, select_evidence
from internal_kb_qa.core.hit import Hit


def _hits(*scores: float) -> list[Hit]:
    return [Hit(text=str(index), score=score) for index, score in enumerate(scores)]


def test_weak_results_are_rejected():
    assert select_evidence(_hits(0.0559, 0.0300, 0.0204)) == []
    assert evaluate_confidence(_hits(0.0559)).reason == "insufficient_evidence"


def test_weak_tail_does_not_dilute_one_strong_source():
    evidence = select_evidence(_hits(0.9946, 0.1080, 0.0603))
    result = evaluate_confidence(evidence)
    assert [hit.score for hit in evidence] == [0.9946]
    assert result.level == "high"
    assert result.evidence_count == 1


def test_multiple_strong_sources_remain_high():
    result = evaluate_confidence(_hits(0.9200, 0.8073, 0.6653, 0.2416))
    assert result.level == "high"
    assert result.evidence_count == 3


def test_moderate_source_is_not_overstated():
    result = evaluate_confidence(_hits(0.6254, 0.4124, 0.0205))
    assert result.level == "medium"
    assert result.evidence_count == 2


def test_result_is_order_independent():
    assert evaluate_confidence(_hits(0.14, 0.157)).score == evaluate_confidence(_hits(0.157, 0.14)).score
