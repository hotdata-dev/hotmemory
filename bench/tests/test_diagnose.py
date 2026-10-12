from datetime import UTC, datetime, timedelta

from bench.diagnose import block_ids, verdict
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
