import math

import pytest

from bench.metrics import dcg, mean, ndcg, score


def test_dcg_by_hand() -> None:
    assert dcg([], 3) == 0.0
    assert dcg([1.0, 1.0, 1.0], 3) == pytest.approx(1 + 1 / 1 + 1 / math.log2(3))
    assert dcg([0.0, 0.0, 1.0, 1.0], 3) == pytest.approx(1 / math.log2(3))


def test_ndcg_by_hand() -> None:
    ranked = ["a", "x", "b", "y"]
    assert ndcg(ranked, {"a", "b"}, 3) == pytest.approx(1.6309297535714575 / 2)
    assert ndcg(ranked, {"a", "b"}, 1) == 1.0
    assert ndcg(ranked, {"b"}, 3) == pytest.approx(1 / math.log2(3))
    assert ndcg(ranked, {"b"}, 2) == 0.0
    assert ndcg(ranked, set(), 3) == 0.0


def test_ndcg_ideal_counts_gold_missing_from_the_ranking() -> None:
    assert ndcg(["a", "x"], {"a", "z"}, 2) == pytest.approx(1 / 2)


def test_score_by_hand() -> None:
    result = score(["a", "x", "b", "y"], {"a", "b"}, ks=(1, 3))
    assert result == {
        "recall_any@1": 1.0,
        "recall_all@1": 0.0,
        "ndcg_any@1": 1.0,
        "recall_any@3": 1.0,
        "recall_all@3": 1.0,
        "ndcg_any@3": pytest.approx(0.8154648767857288),
    }


def test_score_with_no_hit() -> None:
    result = score(["x", "y"], {"a"}, ks=(1, 50))
    assert result["recall_any@50"] == 0.0
    assert result["recall_all@50"] == 0.0
    assert result["ndcg_any@50"] == 0.0


def test_score_has_every_k_by_default() -> None:
    assert len(score(["a"], {"a"})) == 18


def test_mean() -> None:
    assert mean([]) == {}
    assert mean([{"m": 1.0}, {"m": 0.0}, {"m": 0.5}]) == {"m": 0.5}
