import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from bench import prompts
from bench.data import QUESTION_TYPES, Question, load
from bench.e2e import choose_sample, estimate, run_question, summarize
from bench.models import EXTRACTOR, JUDGE, READER, CachedModels, Reply
from hotmemory import MemoryStore
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


def test_run_question_on_every_arm(questions: dict[str, Question]) -> None:
    question = questions["q_one"]
    store = MemoryStore(embedder=fake_embedder, clock=FakeClock())
    models = FakeModels()
    emptied: list[Sequence[str]] = []

    def empty(namespace: Sequence[str]) -> None:
        emptied.append(namespace)
        for record in store.list(namespace, limit=1000):
            store.delete(record.namespace, record.key)

    result = run_question(question, models, store, empty, "r1")
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


def test_estimate_counts_each_arm(questions: dict[str, Question]) -> None:
    class Chars:
        def count(self, text: str) -> int:
            return len(text)

        def truncate(self, text: str, limit: int) -> str:
            return text

    cost = estimate(list(questions.values()), Chars())
    assert set(cost) == {"full", "retrieval", "memory"}
    assert all(value > 0 for value in cost.values())


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
