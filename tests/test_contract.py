"""Tests of the memory contract. Each test runs `Memory` over every driver."""

from datetime import UTC, datetime, timedelta

import pytest

from hotmemory import Fact, Memory, Record, Store
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


@pytest.mark.parametrize("bad", ["team/alerts/cpu", "team//cpu@1", "team/alerts/@1", "cpu@1"])
def test_forget_checks_every_id_before_it_deletes(memory: Memory, bad: str) -> None:
    memory.store.put(SCOPE, "disk", kind="fact", content="disk at night")
    memory.store.put(SCOPE, "cpu", kind="fact", content="cpu at noon")

    with pytest.raises(ValueError):
        memory.forget([("team",)], ids=["team/alerts/disk@1", bad])
    assert memory.store.get(SCOPE, "disk") is not None
    assert memory.store.get(SCOPE, "cpu") is not None


def test_profile_count_shares_its_window_with_sub_namespaces(
    memory: Memory, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    monkeypatch.setattr("hotmemory.contract.COUNT_LIMIT", 3)
    memory.remember([Fact(kind="fact", content="disk at night")], SCOPE)
    clock.advance()
    child = (*SCOPE, "night")
    memory.remember([Fact(kind="fact", content=f"net fact {n}") for n in range(3)], child)

    _, block = memory.profile("disk", [SCOPE])
    assert block.split("\n")[-2:] == ["- team/alerts: 0+", "- team/alerts/night: 3+"]


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


def test_profile_groups_the_subject_by_kind_and_ends_with_counts(
    memory: Memory, clock: FakeClock
) -> None:
    other = ("team", "alerts", "night")
    memory.remember([Fact(kind="procedure", subject="disk", content="Rotate the logs.")], SCOPE)
    clock.advance()
    memory.remember(
        [
            Fact(kind="fact", subject="disk", content="The disk fills at night."),
            Fact(kind="fact", subject="cpu", content="The CPU spikes at noon."),
        ],
        SCOPE,
    )
    clock.advance()
    memory.remember(
        [Fact(kind="fact", subject="disk", content="The disk is a 2 TB volume.")], other
    )

    records, block = memory.profile("disk", [SCOPE])
    assert [(record.kind, record.content) for record in records] == [
        ("fact", "The disk is a 2 TB volume."),
        ("fact", "The disk fills at night."),
        ("procedure", "Rotate the logs."),
    ]
    lines = block.split("\n")
    assert [line for line in lines if not line.startswith("- ")] == [
        "fact:",
        "procedure:",
        "namespaces:",
    ]
    assert lines[-2:] == ["- team/alerts: 3", "- team/alerts/night: 1"]


def test_profile_counts_show_the_list_limit(
    memory: Memory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hotmemory.contract.COUNT_LIMIT", 2)
    facts = [Fact(kind="fact", subject="disk", content=f"disk fact {n}") for n in range(3)]
    memory.remember(facts, SCOPE)

    _, block = memory.profile("cpu", [SCOPE])
    assert block == "namespaces:\n- team/alerts: 2+"


def test_profile_keeps_the_counts_inside_the_budget(memory: Memory) -> None:
    facts = [Fact(kind="fact", subject="disk", content=f"disk fact {n}") for n in range(3)]
    memory.remember(facts, SCOPE)
    tail = "namespaces:\n- team/alerts: 3"

    records, block = memory.profile("disk", [SCOPE], budget=len(tail) + 1)
    assert (records, block) == ([], tail)
    records, block = memory.profile("disk", [SCOPE], budget=len(tail) - 1)
    assert (records, block) == ([], "")
    full_records, full = memory.profile("disk", [SCOPE])
    first_two = full.split("\n")[:2]
    budget = len("\n".join(first_two)) + 1 + len(tail)
    records, block = memory.profile("disk", [SCOPE], budget=budget)
    assert block == "\n".join([*first_two, tail])
    assert records == full_records[:1]


def test_capture_remembers_what_the_extractor_returns(memory: Memory) -> None:
    memory.remember([Fact(kind="fact", subject="disk", content="The disk fills at night.")], SCOPE)
    seen: list[tuple[str, object, list[str]]] = []
    observed = START - timedelta(minutes=5)
    stated = START - timedelta(hours=1)

    def extractor(text: str, observed_at: object, current: list[Record]) -> list[Fact]:
        seen.append((text, observed_at, [record.content for record in current]))
        return [
            Fact(kind="fact", subject="disk", content="The disk fills at noon."),
            Fact(kind="fact", subject="cpu", content="The CPU spikes.", observed_at=stated),
        ]

    ids = memory.capture(
        "disk fills", SCOPE, extractor, actor="fake-extractor", observed_at=observed
    )

    assert seen == [("disk fills", observed, ["The disk fills at night."])]
    records = [memory.store.get(SCOPE, record_id.split("/")[-1].split("@")[0]) for record_id in ids]
    assert [(r.content, r.actor, r.observed_at) for r in records if r is not None] == [
        ("The disk fills at noon.", "fake-extractor", observed),
        ("The CPU spikes.", "fake-extractor", stated),
    ]


def test_capture_with_no_facts_writes_nothing(memory: Memory) -> None:
    assert memory.capture("nothing here", SCOPE, lambda text, observed_at, current: []) == []
    assert memory.store.list(SCOPE) == []


def test_recall_and_profile_leave_episodes_out(memory: Memory) -> None:
    memory.store.put(
        SCOPE, "thread-0001", kind="episode", subject="disk", content="The disk fills at night."
    )
    memory.store.put((*SCOPE, "chat"), "thread-0002", kind="episode", content="The disk is full.")
    [fact] = memory.remember([Fact(kind="fact", subject="disk", content="The disk fills.")], SCOPE)

    records, block = memory.recall("The disk fills at night.", [SCOPE])
    assert [record.id for record in records] == [fact]
    assert "night" not in block
    records, block = memory.profile("disk", [SCOPE])
    assert [record.id for record in records] == [fact]
    assert block.split("\n")[-3:] == ["namespaces:", "- team/alerts: 1", "- team/alerts/chat: 0"]


PINNED_RECALL = (
    "- The disk fills at night. [sources: chat-1, wiki] "
    "[valid: 2026-10-01T00:00:00Z to 2026-10-04T00:00:00Z]\n"
    "- The disk fills. [sources: none] [valid: unknown to now]\n"
    "- The disk is a 2 TB volume. [sources: inventory] [valid: 2026-09-01T08:30:00Z to now]"
)

PINNED_PROFILE = (
    "fact:\n"
    "- The disk is a 2 TB volume. [sources: inventory] [valid: 2026-09-01T08:30:00Z to now]\n"
    "- The disk fills. [sources: none] [valid: unknown to now]\n"
    "- The disk fills at night. [sources: chat-1, wiki] "
    "[valid: 2026-10-01T00:00:00Z to 2026-10-04T00:00:00Z]\n"
    "procedure:\n"
    "- Rotate the logs before noon. [sources: runbook] [valid: unknown to now]\n"
    "namespaces:\n"
    "- team/alerts: 5\n"
    "- team/alerts/night: 1"
)


def remember_pinned(memory: Memory, clock: FakeClock) -> None:
    facts = [
        Fact(
            kind="fact",
            subject="disk",
            content="The disk fills at night.",
            sources=("chat-1", "wiki"),
            valid_from=datetime(2026, 10, 1, tzinfo=UTC),
            valid_until=datetime(2026, 10, 4, tzinfo=UTC),
        ),
        Fact(kind="fact", subject="disk", content="The disk fills."),
        Fact(
            kind="fact",
            subject="disk",
            content="The disk is a 2 TB volume.",
            sources=("inventory",),
            valid_from=datetime(2026, 9, 1, 8, 30, tzinfo=UTC),
        ),
        Fact(
            kind="procedure",
            subject="disk",
            content="Rotate the logs before noon.",
            sources=("runbook",),
        ),
        Fact(kind="fact", subject="cpu", content="The CPU spikes at noon."),
    ]
    for fact in facts:
        memory.remember([fact], SCOPE)
        clock.advance()
    memory.remember(
        [Fact(kind="fact", subject="net", content="The link drops.")], (*SCOPE, "night")
    )
    memory.store.put(
        SCOPE, "thread-0001", kind="episode", subject="disk", content="The disk fills at night."
    )


def test_recall_block_matches_its_pinned_string(memory: Memory, clock: FakeClock) -> None:
    remember_pinned(memory, clock)
    records, block = memory.recall("The disk fills at night.", [SCOPE], k=3)
    assert block == PINNED_RECALL
    assert [record.content for record in records] == [
        "The disk fills at night.",
        "The disk fills.",
        "The disk is a 2 TB volume.",
    ]


def test_profile_block_matches_its_pinned_string(memory: Memory, clock: FakeClock) -> None:
    remember_pinned(memory, clock)
    _, block = memory.profile("disk", [SCOPE])
    assert block == PINNED_PROFILE


def test_memory_reads_only_the_allowed_scopes(memory: Memory) -> None:
    other = ("team", "billing")
    memory.remember([Fact(kind="fact", subject="disk", content="The disk fills at night.")], SCOPE)
    memory.remember([Fact(kind="fact", subject="disk", content="The disk bill is due.")], other)

    records, _ = memory.recall("disk", [SCOPE])
    assert [record.namespace for record in records] == [SCOPE]
    hits = memory.candidates(Fact(kind="fact", content="The disk bill is due."), [SCOPE])
    assert [hit.record.namespace for hit in hits] == [SCOPE]
    _, block = memory.profile("disk", [SCOPE])
    assert "billing" not in block
    assert "bill" not in block
    assert memory.recall("disk", []) == ([], "")
