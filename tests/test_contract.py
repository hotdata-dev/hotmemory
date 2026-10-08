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
