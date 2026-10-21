from pathlib import Path

from bench.data import load
from bench.scale import BUCKETS, Run, bucket, namespace_of, pool, rows_of, same_top, scopes
from hotmemory import MemoryStore
from tests.conftest import fake_embedder

FIXTURE = Path(__file__).with_name("fixture.json")


def test_pool_puts_the_first_questions_first_and_keeps_each_session_once() -> None:
    questions = load(FIXTURE, expected_sha256=None)
    first = questions[-1:]
    sessions = pool(questions, first)
    ids = [session.id for session in sessions]
    assert len(ids) == len(set(ids))
    evidence = first[0].evidence
    assert set(ids[: len(evidence)]) == evidence
    near = {session.id for session in first[0].sessions if session.turns} - evidence
    assert set(ids[len(evidence) : len(evidence) + len(near)]) == near


def test_rows_share_the_bucket_of_their_session() -> None:
    questions = load(FIXTURE, expected_sha256=None)
    for row in rows_of(pool(questions, [])):
        assert row.namespace == namespace_of(bucket(row.session))
        assert 0 <= bucket(row.session) < BUCKETS


def test_scopes_nest() -> None:
    found = scopes()
    assert found["1%"][: len(found["10%"])] == found["10%"]
    assert found["10%"][: len(found["100%"])] == found["100%"]


def test_same_top_tells_ties_from_differences() -> None:
    assert same_top([("a", 0.1), ("b", 0.2)], [("a", 0.1), ("b", 0.2)]) == "equal"
    assert same_top([("b", 0.1), ("a", 0.1)], [("a", 0.1), ("b", 0.1)]) == "ties"
    assert same_top([("c", 0.3)], [("a", 0.1)]) == "differ"


def test_run_on_memory_store_matches_the_exact_ranking() -> None:
    questions = load(FIXTURE, expected_sha256=None)
    scored = [question for question in questions if question.is_scored]
    rows = list(rows_of(pool(questions, scored)))
    store = MemoryStore(embedder=fake_embedder)
    run = Run({"vector": store}, fake_embedder, scored, len(rows))
    run.fill(rows)
    result = run.checkpoint()
    assert result["rows"] == len(rows)
    whole = result["scopes"]["100%"]
    assert whole["rows"] == len(rows)
    assert whole["vector"]["exact"]["differ"] == 0
    assert set(whole["vector"]["metrics"]) == {"recall_any@5", "recall_any@10"}
    assert len(run.flushes) == 1
