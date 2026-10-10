"""The retrieval metrics of the reference code: recall_any@k, recall_all@k, and ndcg_any@k.

The formulas follow `src/retrieval/eval_utils.py` of the LongMemEval repository.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Mapping, Sequence

KS = (1, 3, 5, 10, 30, 50)
METRICS = ("recall_any", "recall_all", "ndcg_any")


def dcg(relevances: Sequence[float], k: int) -> float:
    """Return the discounted cumulative gain of the first `k` relevances.

    The first relevance counts in full, and the relevance at position i, from 1, is
    divided by log2(i + 1) for i of 2 or more.
    """
    top = relevances[:k]
    if not top:
        return 0.0
    return top[0] + sum(value / math.log2(index + 1) for index, value in enumerate(top) if index)


def ndcg(ranked: Sequence[str], gold: Collection[str], k: int) -> float:
    """Return the normalized DCG at `k` of `ranked` against the ids in `gold`.

    The ideal ranking puts every id of `gold` first. With no gold id, the value is 0.
    """
    ideal = dcg([1.0] * len(gold), k)
    if ideal == 0:
        return 0.0
    return dcg([1.0 if item in gold else 0.0 for item in ranked], k) / ideal


def score(ranked: Sequence[str], gold: Collection[str], ks: Iterable[int] = KS) -> dict[str, float]:
    """Return `recall_any@k`, `recall_all@k`, and `ndcg_any@k` for each k in `ks`.

    `recall_any@k` is 1 if one gold id is in the first k of `ranked`, and `recall_all@k`
    is 1 if every gold id is there.
    """
    result: dict[str, float] = {}
    for k in ks:
        top = set(ranked[:k])
        result[f"recall_any@{k}"] = float(any(item in top for item in gold))
        result[f"recall_all@{k}"] = float(all(item in top for item in gold))
        result[f"ndcg_any@{k}"] = ndcg(ranked, gold, k)
    return result


def mean(rows: Iterable[Mapping[str, float]]) -> dict[str, float]:
    """Return the mean of each metric over `rows`. Every row must have the same names."""
    rows = list(rows)
    if not rows:
        return {}
    names = list(rows[0])
    return {name: sum(row[name] for row in rows) / len(rows) for name in names}
