import json
import threading
from pathlib import Path
from typing import Any

import pytest

from bench.beam import Conversation, parse_conversation
from bench.beam_e2e import (
    CONFIG,
    NAMESPACE,
    Block,
    Job,
    SpendLimit,
    ThreadModels,
    capture_conversation,
    choose,
    chunks_block,
    coverage,
    estimate,
    longest_worker,
    read_rows,
    run_on_store,
    source_windows,
    summarize,
)
from bench.beam_score import JUDGE, MATCH_SYSTEM
from bench.models import BEAM_MINI, EXTRACTOR, Reply
from bench.tests.test_beam import row
from hotmemory import Memory, MemoryStore
from tests.conftest import FakeClock, fake_embedder


class FakeModels:
    """Facts from each user line, a fixed reader answer, and full marks from the judge."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.answers = 0
        self.lock = threading.Lock()

    def __call__(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        schema: dict[str, Any] | None = None,
        system: str | None = None,
    ) -> Reply:
        with self.lock:
            self.calls.append(model)
        if model == EXTRACTOR:
            session = prompt.split("Session:\n", 1)[1]
            facts = [
                {"subject": "user", "content": line.removeprefix("user: ")}
                for line in session.split("\n\n")
                if line.startswith("user: ")
            ]
            return Reply(json.dumps({"facts": facts}), 100, 10)
        if model == JUDGE and system == MATCH_SYSTEM:
            return Reply("NO", 10, 1)
        if model == JUDGE and "RUBRIC CRITERION" in prompt:
            return Reply('{"score": 1.0, "reason": "ok"}', 100, 10)
        with self.lock:
            self.answers += 1
        return Reply("The answer.", 1000, 5)


@pytest.fixture
def conversation() -> Conversation:
    return parse_conversation(row())


def job(tmp_path: Path) -> Job:
    rows_file = tmp_path / "rows.jsonl"
    rows_file.write_text(json.dumps({"header": {"run": 1}}) + "\n")
    return Job("", ("memory", "chunks"), ("mini",), False, "kept", rows_file)


def store() -> MemoryStore:
    return MemoryStore(embedder=fake_embedder, clock=FakeClock())


def test_a_conversation_is_captured_once_and_every_probe_is_scored(
    tmp_path: Path, conversation: Conversation
) -> None:
    fake = FakeModels()
    models = ThreadModels(fake, tmp_path / "replies.sqlite")
    found = job(tmp_path)
    memory = store()
    done = run_on_store(found, conversation, memory, models, True, frozenset())
    assert done.probes == 20
    assert done.capture["windows"] == 2
    assert done.capture["windows_at_limit"] == 0
    assert done.capture["facts_written"] == 3
    header, captures, probes = read_rows(found.rows_file)
    assert header == {"run": 1}
    assert [c["conversation"] for c in captures] == ["7"]
    assert len(probes) == 20
    first = probes[0]
    assert set(first["combos"]) == {"memory/mini", "chunks/mini"}
    assert first["arms"]["memory"]["facts_in_block"] == 3
    assert first["arms"]["chunks"]["facts_in_block"] == 0
    assert first["arms"]["chunks"]["episodes_in_block"]
    assert first["arms"]["memory"]["source_windows"] == ["window-01-0001", "window-02-0001"]
    nugget_rows = [p for p in probes if p["ability"] != "event_ordering"]
    assert all(c["score"] == 1.0 for p in nugget_rows for c in p["combos"].values())
    ordering = [p for p in probes if p["ability"] == "event_ordering"]
    assert all(c["tau_norm"] is not None for p in ordering for c in p["combos"].values())
    assert fake.calls.count(EXTRACTOR) == 2
    assert fake.answers == 40
    summaries = summarize(probes, ("memory", "chunks"), ("mini",))
    assert summaries["memory/mini"]["count"] == 20
    assert summaries["memory/mini"]["by_domain"].keys() == {"Coding"}
    models.close()


def test_a_resumed_conversation_runs_only_the_probes_left(
    tmp_path: Path, conversation: Conversation
) -> None:
    fake = FakeModels()
    models = ThreadModels(fake, tmp_path / "replies.sqlite")
    memory = store()
    capture_conversation(memory, models, conversation.windows(), (*NAMESPACE, "7"))
    calls = len(fake.calls)
    finished = frozenset(probe.id for probe in conversation.probes[:18])
    found = job(tmp_path)
    done = run_on_store(found, conversation, memory, models, False, finished)
    assert done.probes == 2
    assert done.capture == {}
    assert EXTRACTOR not in fake.calls[calls:]
    _, captures, probes = read_rows(found.rows_file)
    assert captures == []
    assert [p["id"] for p in probes] == [p.id for p in conversation.probes[18:]]
    models.close()


def test_a_second_capture_skips_the_stored_windows(
    tmp_path: Path, conversation: Conversation
) -> None:
    fake = FakeModels()
    models = ThreadModels(fake, tmp_path / "replies.sqlite")
    memory = store()
    namespace = (*NAMESPACE, "7")
    capture_conversation(memory, models, conversation.windows(), namespace)
    again = capture_conversation(memory, models, conversation.windows(), namespace)
    assert again["facts_written"] == 0
    assert fake.calls.count(EXTRACTOR) == 2
    models.close()


def test_thread_models_sum_every_thread_and_stop_at_the_limit(tmp_path: Path) -> None:
    models = ThreadModels(FakeModels(), tmp_path / "replies.sqlite", limit=1.0)
    threads = [threading.Thread(target=models, args=(BEAM_MINI, f"p{n}", 10)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert models.calls == 4
    assert models.spent == pytest.approx(4 * (1000 * 0.40 + 5 * 1.60) / 1e6)
    paid: list[str] = []

    def costly(model: str, prompt: str, *_: Any, **__: Any) -> Reply:
        paid.append(prompt)
        return Reply("x", 10_000_000, 0)

    big = ThreadModels(costly, tmp_path / "r2.sqlite", 0.5)
    with pytest.raises(SpendLimit):
        big(BEAM_MINI, "p", 10)
    with pytest.raises(SpendLimit):
        big(BEAM_MINI, "q", 10)
    assert paid == ["p"]
    models.close()
    big.close()


def test_the_chunks_block_keeps_to_the_budget(tmp_path: Path, conversation: Conversation) -> None:
    memory = store()
    namespace = (*NAMESPACE, "7")
    Memory(memory).capture(
        "\n\n".join(["word " * 190] * 25),
        namespace,
        lambda text, at, known: [],
        episode="window-01-0001",
        chunk_chars=CONFIG.chunk_chars,
    )
    block = chunks_block(memory, namespace, "word")
    assert 0 < len(block.text) <= CONFIG.budget
    assert len(block.episodes) < 25
    assert all(key.startswith("window-01-0001-") for key in block.episodes)


def test_coverage_counts_the_source_windows_in_the_block(conversation: Conversation) -> None:
    windows = conversation.windows(limit=60)
    probe = conversation.probes[0]
    found = source_windows(windows, probe)
    assert found == {"window-01-0001", "window-01-0002", "window-02-0001"}
    block = Block("", ("window-01-0001-0001", "window-01-0001-0002", "window-02-0001-0001"), 0)
    assert coverage(found, block) == pytest.approx(2 / 3)
    assert coverage(set(), block) is None


def test_the_estimate_counts_each_part(conversation: Conversation) -> None:
    both = estimate([conversation], ("memory", "chunks"), ("mini",), 1)
    assert both.parts["extraction"] == pytest.approx(2 * 0.0005)
    assert both.parts["readers"] > 0 and both.parts["judge"] > 0
    assert both.total == pytest.approx(sum(both.by_conversation.values()))
    reused = estimate([conversation], ("memory",), ("mini",), 1, capture=False)
    assert reused.parts["extraction"] == reused.parts["embeddings"] == 0
    assert reused.parts["judge"] == pytest.approx(both.parts["judge"] / 2)


def test_the_longest_worker_takes_the_longest_jobs_first() -> None:
    assert longest_worker([3, 5, 3, 4], 2) == 8
    assert longest_worker([3, 5, 3, 4], 1) == 15
    assert longest_worker([], 4) == 0


def test_a_sample_needs_the_sample_split(conversation: Conversation) -> None:
    assert choose("1M", "all", [conversation]) == [conversation]
    with pytest.raises(ValueError, match="needs --split 100K"):
        choose("1M", "a", [conversation])
