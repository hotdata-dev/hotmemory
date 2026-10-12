from pathlib import Path

import pytest

from bench.data import load
from bench.retrieval import memory_target, retried, run, summarize
from tests.conftest import fake_embedder

FIXTURE = Path(__file__).with_name("fixture.json")


def test_run_isolated_on_memory_store() -> None:
    questions = [q for q in load(FIXTURE, expected_sha256=None) if q.is_scored]
    target = memory_target(fake_embedder)
    rows, timings = run(questions, target, "isolated", "r1")
    assert [row["id"] for row in rows] == ["q_one"]
    ranked = [session for session, _ in rows[0]["ranked"]["vector"]]
    assert sorted(ranked) == sorted(s.id for s in questions[0].episodes())
    assert target.stores["vector"].list(("bench",)) == []
    assert timings["delete_s"]["count"] == 1
    summary = summarize(rows, "vector")
    assert summary["questions"] == 1
    assert summary["all"]["recall_all@50"] == 1.0
    assert summary["by_type"]["multi-session"]["questions"] == 1


def test_run_shared_keeps_every_question() -> None:
    questions = load(FIXTURE, expected_sha256=None)
    target = memory_target(fake_embedder)
    rows, timings = run(questions, target, "shared", "r1")
    assert [row["id"] for row in rows] == ["q_one", "q_two_abs", "q_three"]
    assert len(target.stores["vector"].list_namespaces(("bench",))) == 3
    assert timings["delete_s"] == {"count": 0}
    for row, question in zip(rows, questions, strict=True):
        assert {s for s, _ in row["ranked"]["vector"]} == {s.id for s in question.episodes()}


def test_retried_runs_again_on_a_dropped_connection() -> None:
    target = memory_target(fake_embedder)
    calls: list[int] = []

    def flaky() -> None:
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("Remote end closed connection without response")

    retried(flaky, target)
    assert len(calls) == 3
    assert target.engine["retries"] == 2


def test_retried_raises_other_errors() -> None:
    target = memory_target(fake_embedder)

    def broken() -> None:
        raise RuntimeError("Bad Request")

    with pytest.raises(RuntimeError, match="Bad Request"):
        retried(broken, target)
