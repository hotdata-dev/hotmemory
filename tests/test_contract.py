"""Tests of the memory contract. Each test runs `Memory` over every driver."""

from datetime import timedelta

import pytest

from hotmemory import Fact, Memory, Store
from hotmemory.contract import derive_key

from .conftest import START, FakeClock

SCOPE = ("team", "alerts")


@pytest.fixture
def memory(store: Store, clock: FakeClock) -> Memory:
    return Memory(store, clock=clock)


@pytest.mark.parametrize(
    ("subject", "content", "key"),
    [
        ("disk", "The disk fills at night.", "disk-"),
        ("host a.b/c@d", "The disk fills at night.", "host-a-b-c-d-"),
        ("", "The disk fills at night.", "fact-"),
        ("x" * 80, "The disk fills at night.", "x" * 64 + "-"),
    ],
)
def test_derived_key_has_a_subject_and_a_content_hash(subject: str, content: str, key: str) -> None:
    derived = derive_key(subject, content)
    assert derived.startswith(key)
    digest = derived.removeprefix(key)
    assert len(digest) == 16
    assert set(digest) <= set("0123456789abcdef")
    assert derive_key(subject, "  the DISK fills\n at night. ") == derived
    assert derive_key(subject, "The disk fills at noon.") != derived


def test_remember_writes_each_fact_under_a_derived_key(memory: Memory) -> None:
    facts = [
        Fact(kind="fact", subject="disk", content="The disk fills at night.", sources=("chat-1",)),
        Fact(kind="procedure", content="Rotate the logs before noon."),
    ]
    ids = memory.remember(facts, SCOPE, actor="extractor")

    keys = [derive_key("disk", facts[0].content), derive_key("", facts[1].content)]
    assert ids == [f"team/alerts/{key}@1" for key in keys]
    records = [memory.store.get(SCOPE, key) for key in keys]
    assert [(r.kind, r.subject, r.actor, r.sources) for r in records if r is not None] == [
        ("fact", "disk", "extractor", ("chat-1",)),
        ("procedure", "", "extractor", ()),
    ]


def test_retried_remember_writes_nothing_new(memory: Memory, clock: FakeClock) -> None:
    facts = [Fact(kind="fact", subject="disk", content="The disk fills at night.")]
    first = memory.remember(facts, SCOPE)
    clock.advance()
    again = memory.remember(facts + facts, SCOPE)

    assert again == first + first
    assert len(memory.store.history(SCOPE, derive_key("disk", facts[0].content))) == 1


def test_remember_writes_nothing_when_one_fact_is_refused(memory: Memory) -> None:
    facts = [
        Fact(kind="fact", subject="disk", content="The disk fills at night."),
        Fact(kind="fact", subject="cpu", content="   "),
    ]
    with pytest.raises(ValueError, match="content"):
        memory.remember(facts, SCOPE)
    assert memory.store.list(SCOPE) == []


def test_remember_keeps_the_fact_times(memory: Memory) -> None:
    observed = START - timedelta(hours=1)
    fact = Fact(kind="fact", content="The disk fills at night.", observed_at=observed)
    [record_id] = memory.remember([fact], SCOPE)

    record = memory.store.get(SCOPE, derive_key("", fact.content))
    assert record is not None
    assert (record.id, record.observed_at, record.valid_from) == (record_id, observed, observed)


def test_recall_keeps_the_records_valid_at_as_of(memory: Memory) -> None:
    t1, t2, t3 = START - timedelta(days=3), START - timedelta(days=2), START - timedelta(days=1)
    facts = [
        Fact(kind="fact", subject="a", content="disk alpha", valid_from=t1),
        Fact(kind="fact", subject="b", content="disk bravo", valid_from=t2),
        Fact(kind="fact", subject="c", content="disk charlie", valid_from=t3),
        Fact(kind="fact", subject="d", content="disk delta", valid_until=t2),
        Fact(kind="fact", subject="e", content="disk echo"),
    ]
    memory.remember(facts, SCOPE)

    records, _ = memory.recall("disk", [SCOPE], as_of=t2)
    assert sorted(record.subject for record in records) == ["a", "b", "e"]
    everything, _ = memory.recall("disk", [SCOPE])
    assert sorted(record.subject for record in everything) == ["a", "b", "c", "d", "e"]


def test_recall_as_of_skips_a_fact_superseded_after_it(memory: Memory, clock: FakeClock) -> None:
    t1, t3 = START - timedelta(days=3), START - timedelta(days=1)
    [first] = memory.remember([Fact(kind="fact", content="disk at night", valid_from=t1)], SCOPE)
    key = first.split("/")[-1].split("@")[0]
    clock.advance()
    memory.store.put(
        SCOPE, key, kind="fact", content="disk at noon", valid_from=t3, close_previous=True
    )

    records, block = memory.recall("disk", [SCOPE], as_of=t1 + timedelta(days=1))
    assert (records, block) == ([], "")


def test_recall_block_holds_whole_lines_inside_the_budget(memory: Memory) -> None:
    facts = [
        Fact(kind="fact", subject=s, content=f"disk report {s}", sources=(f"chat-{s}",))
        for s in ("a", "b", "c")
    ]
    memory.remember(facts, SCOPE)
    records, block = memory.recall("disk report", [SCOPE], budget=10_000)
    lines = block.split("\n")
    assert len(records) == len(lines) == 3

    two = len(lines[0]) + 1 + len(lines[1])
    for budget, count in ((two, 2), (two - 1, 1), (len(lines[0]) - 1, 0), (0, 0)):
        fitted, text = memory.recall("disk report", [SCOPE], budget=budget)
        assert [record.id for record in fitted] == [record.id for record in records[:count]]
        assert text == "\n".join(lines[:count])
        assert len(text) <= budget


def test_recall_line_carries_content_sources_and_span(memory: Memory) -> None:
    start = START - timedelta(days=1, microseconds=5)
    facts = [
        Fact(
            kind="fact",
            content="disk  fills\nat night",
            sources=("chat-1", "wiki"),
            valid_from=start,
            valid_until=START,
        ),
        Fact(kind="fact", content="disk is quiet"),
    ]
    memory.remember(facts, SCOPE)

    _, block = memory.recall("disk", [SCOPE])
    assert sorted(block.split("\n")) == [
        "- disk fills at night [sources: chat-1, wiki] "
        "[valid: 2026-10-04T11:59:59Z to 2026-10-05T12:00:00Z]",
        "- disk is quiet [sources: none] [valid: unknown to now]",
    ]


def test_candidates_returns_near_records_closest_first(memory: Memory) -> None:
    memory.remember(
        [
            Fact(kind="fact", subject="a", content="disk fills at night"),
            Fact(kind="fact", subject="b", content="disk fills"),
            Fact(kind="fact", subject="c", content="cpu spikes at noon"),
        ],
        SCOPE,
    )
    before = memory.store.list(SCOPE)

    hits = memory.candidates(Fact(kind="fact", content="disk fills at night"), [SCOPE], k=2)
    assert [hit.record.subject for hit in hits] == ["a", "b"]
    assert hits[0].distance is not None and hits[0].distance < 1e-6
    assert memory.store.list(SCOPE) == before


def test_supersede_closes_the_record_and_writes_the_next_revision(
    memory: Memory, clock: FakeClock
) -> None:
    night = START - timedelta(days=2)
    [first] = memory.remember(
        [Fact(kind="fact", subject="disk", content="disk at night", valid_from=night)], SCOPE
    )
    key = derive_key("disk", "disk at night")
    clock.advance()
    noon = clock()
    second = memory.supersede(SCOPE, key, Fact(kind="fact", subject="disk", content="disk at noon"))
    clock.advance()
    retried = memory.supersede(
        SCOPE, key, Fact(kind="fact", subject="disk", content="disk at noon")
    )

    assert (second, retried) == (f"team/alerts/{key}@2", second)
    old, new = memory.store.history(SCOPE, key)
    assert (old.id, old.valid_until, old.expired_at) == (first, noon, noon)
    assert (new.content, new.valid_from, new.valid_until) == ("disk at noon", noon, None)


def test_supersede_refuses_what_it_cannot_close(memory: Memory) -> None:
    fact = Fact(kind="fact", content="disk at noon")
    with pytest.raises(ValueError, match="no current revision"):
        memory.supersede(SCOPE, "missing", fact)
    [first] = memory.remember([Fact(kind="fact", content="disk at night", valid_from=START)], SCOPE)
    key = derive_key("", "disk at night")
    with pytest.raises(ValueError, match="valid_from"):
        memory.supersede(SCOPE, key, fact, valid_from=START - timedelta(seconds=1))
    assert [record.id for record in memory.store.history(SCOPE, key)] == [first]


def test_forget_by_ids_deletes_every_revision_of_each_key(memory: Memory, clock: FakeClock) -> None:
    store = memory.store
    store.put(SCOPE, "disk", kind="fact", content="disk at night")
    clock.advance()
    store.put(SCOPE, "disk", kind="fact", content="disk at noon")
    store.put(SCOPE, "cpu", kind="fact", content="cpu at noon")
    store.put(("team", "other"), "net", kind="fact", content="net at noon")

    with pytest.raises(ValueError, match="outside"):
        memory.forget([SCOPE], ids=["team/alerts/disk@1", "team/other/net@1"])
    assert len(store.history(SCOPE, "disk")) == 2

    deleted = memory.forget([SCOPE], ids=["team/alerts/disk@1"])
    assert deleted == ["team/alerts/disk@1", "team/alerts/disk@2"]
    assert store.history(SCOPE, "disk") == []
    assert store.get(SCOPE, "cpu") is not None


def test_forget_by_horizon_deletes_the_keys_due_before_it(memory: Memory) -> None:
    store = memory.store
    soon, later = START + timedelta(days=1), START + timedelta(days=3)
    store.put(SCOPE, "soon", kind="fact", content="disk soon", forget_after=soon)
    store.put(SCOPE, "later", kind="fact", content="disk later", forget_after=later)
    store.put(SCOPE, "kept", kind="fact", content="disk kept")
    store.put(("team", "other"), "soon", kind="fact", content="net soon", forget_after=soon)

    deleted = memory.forget([SCOPE], horizon=START + timedelta(days=2))
    assert deleted == ["team/alerts/soon@1"]
    assert sorted(record.key for record in store.list(SCOPE)) == ["kept", "later"]
    assert store.get(("team", "other"), "soon") is not None
    with pytest.raises(ValueError, match="exactly one"):
        memory.forget([SCOPE])
