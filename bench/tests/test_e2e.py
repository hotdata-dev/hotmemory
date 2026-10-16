import json
import os
import subprocess
import uuid
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from bench import prompts
from bench.data import QUESTION_TYPES, Question, load
from bench.e2e import (
    SAMPLES,
    Config,
    capture_session,
    choose_sample,
    episode_key,
    estimate,
    existing_databases,
    extractor,
    fact_source,
    kept_name,
    library_commit,
    local_store,
    main,
    run_question,
    sample_ids,
    shards_of,
    summarize,
)
from bench.models import EXTRACTOR, JUDGE, READER, CachedModels, Offline, Reply
from hotmemory import Memory, MemoryStore
from tests.conftest import FakeClock, fake_embedder

FIXTURE = Path(__file__).with_name("fixture.json")


@pytest.fixture
def questions() -> dict[str, Question]:
    return {question.id: question for question in load(FIXTURE, expected_sha256=None)}


class FakeModels:
    """Extracts each user turn as a fact, answers with the prompt's facts, and judges yes."""

    def __init__(self) -> None:
        self.prompts: list[tuple[str, str]] = []

    def __call__(
        self, model: str, prompt: str, max_tokens: int, schema: dict[str, Any] | None = None
    ) -> Reply:
        self.prompts.append((model, prompt))
        if model == EXTRACTOR:
            session = prompt.split("Session:\n", 1)[1]
            facts = [
                {"subject": "user", "content": line.removeprefix("user: ")}
                for line in session.split("\n\n")
                if line.startswith("user: ")
            ]
            return Reply(json.dumps({"facts": facts}), 10, 5)
        if prompt.startswith("I will give you a question") or "unanswerable" in prompt[:40]:
            return Reply("yes", 10, 1)
        return Reply("three bikes", 100, 20)


def test_judge_prompts_follow_the_reference(questions: dict[str, Question]) -> None:
    question = questions["q_one"]
    text = prompts.judge(question, "two")
    assert text.startswith("I will give you a question, a correct answer, and a response")
    assert "Correct Answer: 3\n\nModel Response: two" in text
    assert "unanswerable" in prompts.judge(questions["q_two_abs"], "no idea")
    assert "off-by-one" in prompts.JUDGE["temporal-reasoning"]
    assert set(prompts.JUDGE) == set(QUESTION_TYPES)


def test_reader_prompt_sorts_sessions_and_drops_labels(questions: dict[str, Question]) -> None:
    question = questions["q_one"]
    text = prompts.read_chats(question, list(reversed(question.sessions)))
    assert text.index("Session 1:\nSession Date: 2023/05/01 (Mon) 12:00") > 0
    assert "has_answer" not in text
    assert text.endswith(
        "Current Date: 2023/05/30 (Tue) 09:00\n"
        "Question: How many bikes do I own?\nAnswer (step by step):"
    )


def test_choose_sample_is_fixed(questions: dict[str, Question]) -> None:
    base = questions["q_one"]
    pool = [replace(base, id=f"{kind}-{n}", type=kind) for kind in QUESTION_TYPES for n in range(5)]
    first = choose_sample(pool, per_type=2, seed=1)
    assert first == choose_sample(pool, per_type=2, seed=1)
    assert len(first) == 12
    assert first != choose_sample(pool, per_type=2, seed=2)
    other = choose_sample(pool, per_type=2, seed=1, exclude=first)
    assert len(other) == 12
    assert not set(other) & set(first)


def test_sample_b_leaves_out_sample_a(questions: dict[str, Question], tmp_path: Path) -> None:
    base = questions["q_one"]
    pool = [
        replace(base, id=f"{kind}-{n}", type=kind) for kind in QUESTION_TYPES for n in range(20)
    ]
    (tmp_path / "bench").mkdir()
    a = sample_ids("a", pool, root=tmp_path)
    b = sample_ids("b", pool, root=tmp_path)
    assert len(a) == len(b) == 54
    assert not set(a) & set(b)
    assert json.loads((tmp_path / SAMPLES["b"].file).read_text()) == b
    assert sample_ids("b", pool[:60], root=tmp_path) == b


def test_run_question_on_every_arm(questions: dict[str, Question]) -> None:
    question = questions["q_one"]
    store = MemoryStore(embedder=fake_embedder, clock=FakeClock())
    models = FakeModels()
    emptied: list[Sequence[str]] = []

    def empty(namespace: Sequence[str]) -> None:
        emptied.append(namespace)
        for record in store.list(namespace, limit=1000):
            store.delete(record.namespace, record.key)

    result = run_question(question, models, {"fused": store, "vector": store}, empty, "r1")
    assert set(result) == {"full", "retrieval", "memory"}
    assert all(arm.correct for arm in result.values())
    assert result["memory"].extracted == 6
    assert store.list(("bench",)) == []
    assert [namespace[3] for namespace in emptied] == ["retrieval", "memory"]
    reads = [p for m, p in models.prompts if m == READER and "several facts" in p[:60]]
    memory_prompt = reads[-1]
    assert "- I picked up a folding bike this evening. [sources: none]" in memory_prompt
    extract_calls = [p for m, p in models.prompts if m == EXTRACTOR]
    assert len(extract_calls) == len(question.sessions)
    assert "- My first bike is a mountain bike." in extract_calls[-1]
    rows = [{"type": question.type, "arms": {"full": {"correct": True}}}]
    assert summarize(rows, "full")["all"] == {"questions": 1, "correct": 1, "accuracy": 1.0}
    assert JUDGE == READER


def test_config_refuses_unknown_values() -> None:
    with pytest.raises(ValueError, match="ranking"):
        Config(memory_ranking="bm25")
    with pytest.raises(ValueError, match="variant"):
        Config(extractor="none")
    with pytest.raises(ValueError, match="at least 1"):
        Config(k=0)


def test_run_question_follows_the_config(
    questions: dict[str, Question], monkeypatch: pytest.MonkeyPatch
) -> None:
    question = questions["q_one"]
    monkeypatch.setitem(
        prompts.VARIANTS,
        "terse",
        prompts.Variant("TERSE {known} {date}\nSession:\n{session}", EXTRACTOR),
    )
    fused = MemoryStore(embedder=fake_embedder, clock=FakeClock())
    vector = MemoryStore(embedder=fake_embedder, clock=FakeClock())
    written: list[str] = []

    def empty(namespace: Sequence[str]) -> None:
        for store, name in ((fused, "fused"), (vector, "vector")):
            if store.list(namespace, limit=1000):
                written.append(f"{namespace[3]}:{name}")
            for record in store.list(namespace, limit=1000):
                store.delete(record.namespace, record.key)

    models = FakeModels()
    config = Config(
        retrieval_ranking="vector", memory_ranking="fused", k=2, budget=200, extractor="terse"
    )
    result = run_question(
        question, models, {"fused": fused, "vector": vector}, empty, "r1", config=config
    )
    assert written == ["retrieval:vector", "memory:fused"]
    assert all(p.startswith("TERSE ") for m, p in models.prompts if m == EXTRACTOR)
    assert 0 < result["memory"].context_chars <= 200
    block = [p for m, p in models.prompts if m == READER and "several facts" in p[:60]][-1]
    assert block.count("[sources: none]") <= 2


def test_estimate_counts_each_arm(questions: dict[str, Question]) -> None:
    class Chars:
        def count(self, text: str) -> int:
            return len(text)

        def truncate(self, text: str, limit: int) -> str:
            return text

    cost = estimate(list(questions.values()), Chars())
    assert set(cost) == {"full", "retrieval", "memory"}
    assert all(value > 0 for value in cost.values())
    larger = estimate(list(questions.values()), Chars(), ("memory",), Config(budget=80_000))
    assert larger["memory"] > cost["memory"]


def test_cached_models_call_once(tmp_path: Path) -> None:
    calls: list[str] = []

    def complete(model: str, prompt: str, max_tokens: int, schema: dict[str, Any] | None) -> Reply:
        calls.append(prompt)
        return Reply("yes", 1_000_000, 0)

    models = CachedModels(complete, tmp_path / "replies.sqlite")
    assert models(READER, "p", 10).text == "yes"
    assert models(READER, "p", 10).text == "yes"
    assert models(READER, "p", 11).text == "yes"
    assert calls == ["p", "p"]
    assert models.spent == pytest.approx(5.0)


def test_shards_deal_every_question_once(questions: dict[str, Question]) -> None:
    base = questions["q_one"]
    pool = [replace(base, id=f"q{n}") for n in range(10)]
    shards = shards_of(pool, 4, "r1")
    assert [shard.run_id for shard in shards] == ["r1-w0", "r1-w1", "r1-w2", "r1-w3"]
    dealt = [q.id for shard in shards for q in shard.questions]
    assert sorted(dealt) == sorted(q.id for q in pool)
    assert [q.id for q in shards[1].questions] == ["q1", "q5", "q9"]
    assert [shard.run_id for shard in shards_of(pool, 1, "r1")] == ["r1"]
    assert len(shards_of(pool[:2], 4, "r1")) == 2


def test_each_variant_formats_and_keeps_the_baseline() -> None:
    assert prompts.VARIANTS["baseline"] == prompts.Variant(prompts.EXTRACT, EXTRACTOR)
    for name, variant in prompts.VARIANTS.items():
        text = prompts.extract("user: hi", "2023/05/21 (Sun) 10:00", ["- old fact"], variant.prompt)
        assert text.endswith("Session date: 2023/05/21 (Sun) 10:00\n\nSession:\nuser: hi"), name
        assert "Facts already in memory:\n- old fact\n" in text, name
    detail = prompts.VARIANTS["detail"].prompt
    assert "in passing" in detail
    assert "Keep each item of a list as its own fact" in detail
    assert "absolute date" in detail


def test_fact_source_hides_the_session_id() -> None:
    source = fact_source("answer_a_1")
    assert source == fact_source("answer_a_1")
    assert source != fact_source("answer_a_2")
    assert source.startswith("session:")
    assert len(source) == len("session:") + 12
    assert "answer" not in source


def test_extractor_adds_the_source(questions: dict[str, Question]) -> None:
    session = questions["q_one"].sessions[0]
    text = prompts.session_text(session)
    plain = extractor(FakeModels())(text, session.date, [])
    sourced = extractor(FakeModels(), source="session:abc")(text, session.date, [])
    assert plain and all(fact.sources == () for fact in plain)
    assert [fact.sources for fact in sourced] == [("session:abc",)] * len(plain)


def test_run_question_gives_each_fact_its_session(questions: dict[str, Question]) -> None:
    question = questions["q_one"]
    store = MemoryStore(embedder=fake_embedder, clock=FakeClock())
    models = FakeModels()

    def empty(namespace: Sequence[str]) -> None:
        for record in store.list(namespace, limit=1000):
            store.delete(record.namespace, record.key)

    run_question(
        question, models, {"fused": store, "vector": store}, empty, "r1", ("memory",), Config()
    )
    block = [p for m, p in models.prompts if m == READER and "several facts" in p[:60]][-1]
    late = next(s for s in question.sessions if s.id == "answer_a_late")
    assert f"[sources: {fact_source(late.id)}]" in block
    assert "[sources: none]" not in block
    assert "answer_" not in block


def test_a_reply_at_the_limit_gives_no_facts(questions: dict[str, Question]) -> None:
    session = questions["q_one"].sessions[0]

    def stopped(model: str, prompt: str, max_tokens: int, schema: Any = None) -> Reply:
        return Reply('{"facts": [{"subject": "a", "con', 10, max_tokens, stopped=True)

    at_limit: list[str] = []
    extract = extractor(stopped, source="session:abc", at_limit=at_limit)
    assert extract(prompts.session_text(session), session.date, []) == []
    assert at_limit == ["session:abc"]


def test_cached_models_keep_a_stopped_reply(tmp_path: Path) -> None:
    def complete(model: str, prompt: str, max_tokens: int, schema: dict[str, Any] | None) -> Reply:
        return Reply("{", 1, max_tokens, stopped=True)

    path = tmp_path / "replies.sqlite"
    models = CachedModels(complete, path)
    assert models(EXTRACTOR, "p", 10, {}).stopped
    models.close()
    replay = CachedModels(Offline(), path)
    assert replay(EXTRACTOR, "p", 10, {}).stopped
    with replay._db:
        replay._db.execute(
            "INSERT INTO replies VALUES (?, ?)",
            ("old", json.dumps({"text": "yes", "input_tokens": 1, "output_tokens": 1})),
        )
    found = replay._db.execute("SELECT reply FROM replies WHERE key = 'old'").fetchone()[0]
    assert not Reply(**json.loads(found)).stopped


def test_config_refuses_episodes_without_session_episodes() -> None:
    with pytest.raises(ValueError, match="session_episodes"):
        Config(episodes="cited")
    with pytest.raises(ValueError, match="episodes must be"):
        Config(session_episodes=True, episodes="all")


def test_episode_key_hides_the_session_id() -> None:
    key = episode_key("answer_a_1")
    assert key == "session-" + fact_source("answer_a_1").removeprefix("session:")
    assert "answer" not in key


@pytest.mark.parametrize("episodes", ["cited", "search"])
def test_run_question_recalls_the_sessions_as_episodes(
    questions: dict[str, Question], episodes: str
) -> None:
    question = questions["q_one"]
    store = MemoryStore(embedder=fake_embedder, clock=FakeClock())
    models = FakeModels()

    def empty(namespace: Sequence[str]) -> None:
        for record in store.list(namespace, limit=1000):
            store.delete(record.namespace, record.key)

    config = Config(budget=100_000, session_episodes=True, episodes=episodes)
    result = run_question(
        question, models, {"fused": store, "vector": store}, empty, "r1", ("memory",), config
    )
    block = [p for m, p in models.prompts if m == READER and "several facts" in p[:60]][-1]
    late = next(s for s in question.sessions if s.id == "answer_a_late")
    assert f"[sources: {episode_key(late.id)}@1]" in block
    assert "\nepisodes:\n## session-" in block
    assert f"\n## {episode_key(late.id)}@1 [observed: " in block
    assert "session:" not in block
    assert "answer_" not in block
    assert episode_key(late.id) in result["memory"].episodes
    assert store.list(("bench",)) == []


def test_capture_session_replays_the_same_extractor_prompts(
    questions: dict[str, Question],
) -> None:
    question = questions["q_one"]
    config = Config(session_episodes=True)
    seen: list[list[str]] = []
    for run in ("r1", "r2"):
        models = FakeModels()
        memory = Memory(MemoryStore(embedder=fake_embedder, clock=FakeClock()))
        for session in question.sessions:
            capture_session(memory, ("bench", run, question.id), session, models, config, [])
        seen.append([p for m, p in models.prompts if m == EXTRACTOR])
    assert seen[0] == seen[1]


def test_kept_name_holds_what_decides_the_memory() -> None:
    config = Config(extractor="detail", session_episodes=True, episodes="search", budget=64000)
    name = kept_name("a", config, "b5443ac")
    assert name.startswith("hotmemory-kept-a-detail")
    assert name.endswith("-episodes-fused-b5443ac")
    after_capture = replace(config, episodes="cited", budget=32000, k=10)
    assert kept_name("a", after_capture, "b5443ac") == name
    for other in (
        kept_name("b", config, "b5443ac"),
        kept_name("a", config, "1234567"),
        kept_name("a", replace(config, extractor="baseline"), "b5443ac"),
        kept_name("a", replace(config, memory_ranking="vector"), "b5443ac"),
        kept_name("a", Config(extractor="detail"), "b5443ac"),
        kept_name("a", Config(extractor="detail", fact_sources=False), "b5443ac"),
    ):
        assert other != name


def test_kept_name_changes_with_the_extractor_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Config(extractor="detail")
    name = kept_name("a", config, "b5443ac")
    variant = prompts.VARIANTS["detail"]
    changed = prompts.Variant(variant.prompt + " ", variant.model)
    monkeypatch.setitem(prompts.VARIANTS, "detail", changed)
    assert kept_name("a", config, "b5443ac") != name


def test_library_commit_refuses_a_change_that_is_not_committed(tmp_path: Path) -> None:
    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    git("init", "-q")
    code = tmp_path / "src" / "hotmemory" / "a.py"
    code.parent.mkdir(parents=True)
    code.write_text("x = 1\n")
    git("add", ".")
    git("commit", "-q", "-m", "code")
    first = git("rev-parse", "--short", "HEAD")
    (tmp_path / "other.txt").write_text("y\n")
    git("add", ".")
    git("commit", "-q", "-m", "other")
    assert library_commit(tmp_path) == first
    code.write_text("x = 2\n")
    with pytest.raises(RuntimeError, match="not committed"):
        library_commit(tmp_path)


def test_keep_needs_the_memory_arm_only(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--keep", "--arms", "retrieval,memory"])
    assert "--keep needs --arms memory" in capsys.readouterr().err


def _embed(texts: Sequence[str]) -> list[list[float]]:
    return [[1.0] + [0.0] * 1535 for _ in texts]


@pytest.mark.hotdata
def test_local_store_keeps_the_database_only_on_success() -> None:
    url = os.environ.get("HOTMEMORY_TEST_URL")
    if not url:
        pytest.skip("HOTMEMORY_TEST_URL is not set")
    kept = f"hotmemory-test-kept-{uuid.uuid4().hex[:8]}"
    failed = f"{kept}-failed"
    temporary = f"{kept}-temporary"
    with local_store(url, kept, _embed, keep=True):
        pass
    with pytest.raises(ValueError), local_store(url, failed, _embed, keep=True):
        raise ValueError("stop")
    with local_store(url, temporary, _embed):
        pass
    try:
        assert existing_databases(url, [kept, failed, temporary]) == [kept]
    finally:
        from hotdata_framework import HotdataClient

        client = HotdataClient("local", "local", host=url)
        for database in client.list_managed_databases():
            if database.description == kept:
                client.delete_managed_database(database)
        client.close()
