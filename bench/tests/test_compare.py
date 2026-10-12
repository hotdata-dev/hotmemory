from typing import Any

from bench.compare import compare


def result(rows: dict[str, list[tuple[str, float]]]) -> dict[str, Any]:
    return {"questions": [{"id": q, "ranked": {"vector": r}} for q, r in rows.items()]}


def test_same_order_and_distance_gap() -> None:
    a = result({"q1": [("s1", 0.1), ("s2", 0.2)], "q2": [("s3", 0.3)]})
    b = result({"q1": [("s1", 0.1000004), ("s2", 0.2)]})
    found = compare(a, b)
    assert (found["compared"], found["differ"]) == (1, [])
    assert 3e-7 < found["max_distance_gap"] < 5e-7


def test_order_difference() -> None:
    a = result({"q1": [("s1", 0.1), ("s2", 0.1)]})
    b = result({"q1": [("s2", 0.1), ("s1", 0.1)]})
    assert compare(a, b)["differ"] == ["q1"]
