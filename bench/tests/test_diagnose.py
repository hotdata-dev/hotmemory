from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bench.diagnose import block_ids, group, memory_config, session_of_fact, table, verdict
from bench.e2e import BASELINE, fact_source
from bench.models import READER, CachedModels, CacheMiss, Offline, Reply
from hotmemory import Record

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


def test_session_of_fact_reads_the_source() -> None:
    sessions = {fact_source("answer_a_1"): "answer_a_1"}
    sourced = replace(fact("k"), sources=(fact_source("answer_a_1"),))
    assert session_of_fact(sourced, sessions) == "answer_a_1"
    assert session_of_fact(fact("k"), sessions) is None


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
