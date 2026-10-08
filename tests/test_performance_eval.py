from internal_kb_qa.evaluation.run_load_eval import _percentile


def test_nearest_rank_percentile() -> None:
    values = list(range(1, 21))

    assert _percentile(values, 0.50) == 10
    assert _percentile(values, 0.95) == 19
    assert _percentile([], 0.95) == 0
