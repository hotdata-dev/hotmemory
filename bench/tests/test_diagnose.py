from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bench.data import Question, load
from bench.diagnose import (
    RECALLED,
    block_ids,
    diagnose,
    evidence_in_block,
    group,
    memory_config,
    session_of_fact,
    table,
    verdict,
)
from bench.e2e import BASELINE, KEPT_RUN, Config, episode_key, fact_source, run_question
from bench.models import JUDGE, READER, CachedModels, CacheMiss, Offline, Reply
from bench.tests.test_e2e import FIXTURE, FakeModels
from hotmemory import MemoryStore, Record
from tests.conftest import FakeClock, fake_embedder

AS_OF = datetime(2023, 5, 30, 23, 59, 59, tzinfo=UTC)


def fact(key: str, *, until: datetime | None = None, size: int = 10) -> Record:
    start = datetime(2023, 5, 1, tzinfo=UTC)
    return Record(
        namespace=("bench",),
        key=key,
        revision=1,
        kind="fact",
        content="x" * size,
        created_at=start,
        valid_from=start,
        valid_until=until,
    )


def test_block_follows_the_order_of_recall() -> None:
    ranked = [fact("a"), fact("b", until=AS_OF - timedelta(days=1)), fact("c"), fact("d")]
    assert block_ids(ranked, AS_OF, k=3, budget=10_000) == {"bench/a@1", "bench/c@1"}


def test_block_stops_at_the_budget() -> None:
    ranked = [fact("a", size=40), fact("b", size=40)]
    line = len("- " + "x" * 40 + " [sources: none] [valid: 2023-05-01T00:00:00Z to now]")
    assert block_ids(ranked, AS_OF, k=30, budget=line) == {"bench/a@1"}


def test_verdict() -> None:
    assert verdict(set(), None, set()) == "not extracted"
    assert verdict({"f"}, 3, {"f"}) == "recalled, reader missed"
    assert verdict({"f"}, 3, set()) == "in the top k, cut by as_of or the budget"
    assert verdict({"f"}, 31, set()) == "extracted, ranked below k"
    assert verdict({"f"}, 31, set(), k=40) == "in the top k, cut by as_of or the budget"


def test_memory_config_reads_the_result_file() -> None:
    assert memory_config({"config": {}}) == BASELINE
    found = memory_config(
        {
            "config": {
                "memory": {"ranking": "vector", "k": 50, "budget": 900, "extractor": "baseline"}
            }
        }
    )
    assert (found.memory_ranking, found.k, found.budget) == ("vector", 50, 900)
    assert not found.fact_sources
    memory = {"ranking": "fused", "k": 30, "budget": 8000, "extractor": "detail"}
    sourced = memory_config({"config": {"memory": {**memory, "fact_sources": True}}})
    assert (sourced.extractor, sourced.fact_sources) == ("detail", True)
    assert (sourced.session_episodes, sourced.episodes) == (False, "none")
    episodes = {**memory, "session_episodes": True, "episodes": "search"}
    found = memory_config({"config": {"memory": episodes}})
    assert (found.session_episodes, found.episodes) == (True, "search")


def test_session_of_fact_reads_the_source() -> None:
    sessions = {fact_source("answer_a_1"): "answer_a_1"}
    sourced = replace(fact("k"), sources=(fact_source("answer_a_1"),))
    assert session_of_fact(sourced, sessions) == "answer_a_1"
    assert session_of_fact(fact("k"), sessions) is None
    keys = {episode_key("answer_a_1"): "answer_a_1"}
    cited = replace(fact("k"), sources=(f"bench/q/{episode_key('answer_a_1')}@1",))
    assert session_of_fact(cited, keys) == "answer_a_1"
    chunk = replace(fact("k"), sources=(f"bench/q/{episode_key('answer_a_1')}-0002@1",))
    assert session_of_fact(chunk, keys) == "answer_a_1"


def test_evidence_in_block_matches_the_episode_keys() -> None:
    question = Question(
        id="q",
        type="multi-session",
        question="?",
        answer="!",
        date=AS_OF,
        answer_session_ids=("answer_a_1",),
        sessions=(),
    )
    assert evidence_in_block(question, [episode_key("answer_a_1")])
    assert not evidence_in_block(question, [episode_key("other")])
    assert evidence_in_block(question, [f"{episode_key('answer_a_1')}-0003"])
    assert not evidence_in_block(question, [])


def test_table_counts_the_evidence_episodes() -> None:
    rows: list[dict[str, Any]] = [
        {
            "type": "multi-session",
            "cause": "not extracted",
            "present": None,
            "evidence_in_block": True,
        },
        {
            "type": "multi-session",
            "cause": "not extracted",
            "present": None,
            "evidence_in_block": False,
        },
    ]
    lines = table(rows).splitlines()
    assert lines[0].endswith("| evidence episode in block |")
    assert lines[2] == "| multi-session | 2 | 2 | 0 | 0 | 0 | 0 | 1 |"


def test_table_splits_the_recalled_group() -> None:
    rows: list[dict[str, Any]] = [
        {"type": "multi-session", "cause": "not extracted", "present": None},
        {"type": "multi-session", "cause": "recalled, reader missed", "present": "some"},
        {"type": "knowledge-update", "cause": "recalled, reader missed", "present": "all"},
    ]
    assert group(rows[1]) == "recalled, some present"
    lines = table(rows).splitlines()
    assert lines[2] == "| multi-session | 2 | 1 | 0 | 0 | 0 | 1 |"
    assert lines[3] == "| knowledge-update | 1 | 0 | 0 | 0 | 1 | 0 |"
    assert lines[4] == "| all | 3 | 1 | 0 | 0 | 1 | 1 |"


def test_offline_replays_the_cache_and_stops_at_a_miss(tmp_path: Path) -> None:
    path = tmp_path / "replies.sqlite"
    CachedModels(lambda *_: Reply("yes", 1, 1), path)(READER, "p", 10)
    replay = CachedModels(Offline(), path)
    assert replay(READER, "p", 10).text == "yes"
    with pytest.raises(CacheMiss):
        replay(READER, "q", 10)
    assert (replay.calls, replay.spent) == (0, 0.0)


class FakeMarker:
    """Marks the facts that hold `word`, and says that every needed value is present."""

    def __init__(self, word: str) -> None:
        self.word = word
        self.calls = 0

    def __call__(
        self, model: str, prompt: str, max_tokens: int, schema: dict[str, Any] | None = None
    ) -> Reply:
        assert model == JUDGE
        self.calls += 1
        if "Facts:\n1. " not in prompt:
            return Reply('{"complete": true}', 10, 3)
        lines = prompt.split("Facts:\n", 1)[1].splitlines()
        marked = [n for n, line in enumerate(lines, 1) if self.word in line]
        return Reply(f'{{"facts": {marked}}}', 10, 3)


def test_diagnose_reads_the_memory_of_the_namespace() -> None:
    question = next(q for q in load(FIXTURE, expected_sha256=None) if q.id == "q_one")
    store = MemoryStore(embedder=fake_embedder, clock=FakeClock())
    stores = {"fused": store, "vector": store}
    config = Config(budget=100_000, session_episodes=True, episodes="search")
    result = run_question(
        question, FakeModels(), stores, lambda _: None, KEPT_RUN, ("memory",), config
    )
    namespace = ("bench", "lme_s", KEPT_RUN, "memory", question.id)
    marker = FakeMarker("bike")
    shown = result["memory"].episodes
    row = diagnose(question, stores, namespace, config, marker, shown)  # type: ignore[arg-type]
    assert len(row["holding"]) == 3 and row["facts"] > 3
    assert set(row["holding_sessions"]) == {"answer_a_1", "answer_a_2", "answer_a_late"}
    assert row["cause"] == RECALLED and row["present"] == "all"
    assert row["evidence_in_block"] is True
