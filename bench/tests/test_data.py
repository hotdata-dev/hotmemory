import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bench.data import Question, load, parse_date, sha256
from bench.episodes import scope, session_of, write_episodes
from hotmemory import Filter, MemoryStore
from tests.conftest import FakeClock, fake_embedder

FIXTURE = Path(__file__).with_name("fixture.json")


@pytest.fixture
def questions() -> dict[str, Question]:
    return {question.id: question for question in load(FIXTURE, expected_sha256=None)}


def test_load_checks_the_hash(tmp_path: Path) -> None:
    copy = tmp_path / "data.json"
    copy.write_bytes(FIXTURE.read_bytes())
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert sha256(copy) == digest
    assert len(load(copy, expected_sha256=digest)) == 3
    with pytest.raises(ValueError, match="SHA-256"):
        load(copy, expected_sha256="0" * 64)


def test_load_refuses_a_repeated_question_id(tmp_path: Path) -> None:
    text = FIXTURE.read_text().replace('"q_three"', '"q_one"')
    copy = tmp_path / "data.json"
    copy.write_text(text)
    with pytest.raises(ValueError, match="repeats a question id"):
        load(copy, expected_sha256=None)


def test_dates_are_utc() -> None:
    assert parse_date("2023/05/30 (Tue) 23:40") == datetime(2023, 5, 30, 23, 40, tzinfo=UTC)


def test_sessions_are_sorted_by_date(questions: dict[str, Question]) -> None:
    dates = [session.date for session in questions["q_one"].sessions]
    assert dates == sorted(dates)
    assert questions["q_one"].sessions[0].id == "answer_a_1"


def test_as_of_is_the_end_of_the_question_day(questions: dict[str, Question]) -> None:
    question = questions["q_one"]
    assert question.as_of == datetime(2023, 5, 30, 23, 59, 59, tzinfo=UTC)
    late = question.sessions[-1]
    assert late.id == "answer_a_late"
    assert question.date < late.date <= question.as_of


def test_answer_is_text(questions: dict[str, Question]) -> None:
    assert questions["q_one"].answer == "3"


def test_evidence_follows_the_reference_rule(questions: dict[str, Question]) -> None:
    question = questions["q_one"]
    assert question.evidence == {"answer_a_1", "answer_a_2", "answer_a_late"}
    assert "answer_a_3" in question.answer_session_ids


def test_scored_questions(questions: dict[str, Question]) -> None:
    assert questions["q_one"].is_scored
    assert questions["q_two_abs"].is_abstention
    assert not questions["q_two_abs"].is_scored
    assert not questions["q_three"].is_scored


def test_episodes_skip_empty_and_repeated_sessions(questions: dict[str, Question]) -> None:
    ids = [session.id for session in questions["q_one"].episodes()]
    assert ids == ["answer_a_1", "filler_1", "answer_a_3", "answer_a_2", "answer_a_late"]


def test_session_key_replaces_characters_outside_the_key_set(
    questions: dict[str, Question],
) -> None:
    session = questions["q_one"].sessions[0]
    odd = type(session)(id="a.b/c@d e", date=session.date, turns=session.turns)
    assert odd.key == "a-b-c-d-e"
    assert odd.source == "session:a.b/c@d e"
    assert session_of(("chunk:x", odd.source)) == "a.b/c@d e"
    with pytest.raises(ValueError, match="no session source"):
        session_of(("chunk:x",))


def test_write_episodes(questions: dict[str, Question]) -> None:
    store = MemoryStore(embedder=fake_embedder, clock=FakeClock())
    question = questions["q_one"]
    namespace = scope("r1", question)
    ids = write_episodes(store, namespace, question)
    assert len(ids) == 5
    found = store.list(namespace, Filter(kind="episode"))
    by_key = {record.key: record for record in found}
    first = by_key["answer_a_1"]
    assert first.content == "My first bike is a mountain bike."
    assert first.sources == ("session:answer_a_1",)
    assert first.observed_at == datetime(2023, 5, 1, 12, 0, tzinfo=UTC)
    hits = store.search(question.question, [namespace], Filter(kind="episode"), k=50)
    assert {session_of(hit.record.sources) for hit in hits} == {
        session.id for session in question.episodes()
    }


def test_load_user_only_streams_the_same_questions_with_user_turns() -> None:
    fixture = Path(__file__).with_name("fixture.json")
    full = load(fixture, expected_sha256=None)
    lean = load(fixture, expected_sha256=None, user_only=True)
    assert [q.id for q in lean] == [q.id for q in full]
    for whole, part in zip(full, lean, strict=True):
        assert [s.id for s in part.sessions] == [s.id for s in whole.sessions]
        assert all(t.role == "user" for s in part.sessions for t in s.turns)
        assert [s.user_text for s in part.sessions] == [s.user_text for s in whole.sessions]
        assert part.evidence == whole.evidence
