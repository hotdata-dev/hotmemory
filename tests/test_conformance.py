"""The conformance suite. Each test proves one row of docs/guarantees.md for every driver."""

from datetime import datetime, timedelta
from typing import Any

import pytest

from hotmemory import Filter, JSONValue, Store, TimeRange

from .conftest import START, FakeClock

NS = ("team", "alerts")


def test_second_put_writes_a_new_revision(store: Store, clock: FakeClock) -> None:
    first = store.put(NS, "disk", kind="fact", content="The disk fills at night.")
    clock.advance()
    second = store.put(NS, "disk", kind="fact", content="The disk fills at noon.")

    assert (first, second) == ("team/alerts/disk@1", "team/alerts/disk@2")
    current = store.get(NS, "disk")
    assert current is not None
    assert current.revision == 2
    previous = store.get(NS, "disk", revision=1)
    assert previous is not None
    assert previous.superseded_by == second
    assert [record.id for record in store.history(NS, "disk")] == [first, second]
    assert [record.id for record in store.list(NS)] == [second]
    assert [hit.record.id for hit in store.search("disk fills", [NS])] == [second]


def test_close_previous_closes_the_current_revision(store: Store, clock: FakeClock) -> None:
    night, noon = START - timedelta(days=2), START - timedelta(days=1)
    first = store.put(NS, "disk", kind="fact", content="The disk fills at night.", valid_from=night)
    clock.advance()
    second = store.put(
        NS,
        "disk",
        kind="fact",
        content="The disk fills at noon.",
        valid_from=noon,
        close_previous=True,
    )

    previous = store.get(NS, "disk", revision=1)
    assert previous is not None
    assert (previous.superseded_by, previous.valid_until, previous.expired_at) == (
        second,
        noon,
        clock(),
    )
    current = store.get(NS, "disk")
    assert current is not None
    assert (current.id, current.valid_from, current.valid_until, current.expired_at) == (
        second,
        noon,
        None,
        None,
    )
    assert [record.id for record in store.history(NS, "disk")] == [first, second]


def test_put_without_close_previous_leaves_the_span_open(store: Store, clock: FakeClock) -> None:
    store.put(NS, "disk", kind="fact", content="The disk fills at night.", valid_from=START)
    clock.advance()
    store.put(NS, "disk", kind="fact", content="The disk fills at noon.", valid_from=clock())

    previous = store.get(NS, "disk", revision=1)
    assert previous is not None
    assert (previous.valid_until, previous.expired_at) == (None, None)


@pytest.mark.parametrize("valid_from", [START - timedelta(seconds=1), None])
def test_close_previous_refuses_a_span_it_cannot_close(
    store: Store, clock: FakeClock, valid_from: datetime | None
) -> None:
    first = store.put(NS, "disk", kind="fact", content="The disk fills at night.", valid_from=START)
    clock.advance()
    with pytest.raises(ValueError, match="valid_from"):
        store.put(
            NS,
            "disk",
            kind="fact",
            content="The disk fills at noon.",
            valid_from=valid_from,
            close_previous=True,
        )
    with pytest.raises(ValueError, match="valid_from"), store.writer() as writer:
        writer.put(NS, "cpu", kind="fact", content="The CPU spikes at noon.")
        writer.put(
            NS,
            "disk",
            kind="fact",
            content="The disk fills at noon.",
            valid_from=valid_from,
            close_previous=True,
        )
        writer.flush()

    assert [record.id for record in store.history(NS, "disk")] == [first]
    assert store.get(NS, "cpu") is None
    current = store.get(NS, "disk")
    assert current is not None
    assert (current.valid_until, current.expired_at) == (None, None)


def test_close_previous_on_a_new_key_or_same_content(store: Store, clock: FakeClock) -> None:
    first = store.put(
        NS, "disk", kind="fact", content="The disk fills at night.", close_previous=True
    )
    clock.advance()
    again = store.put(
        NS,
        "disk",
        kind="fact",
        content="the disk fills at night.",
        valid_from=clock(),
        close_previous=True,
    )

    assert again == first == "team/alerts/disk@1"
    current = store.get(NS, "disk")
    assert current is not None
    assert (current.valid_until, current.expired_at) == (None, None)


def test_deduplication_is_exact_on_normalized_content(store: Store, clock: FakeClock) -> None:
    first = store.put(NS, "disk", kind="fact", content="The disk fills at night.")
    clock.advance()
    again = store.put(NS, "disk", kind="fact", content="  the DISK fills\n at night. ")
    clock.advance()
    paraphrase = store.put(NS, "disk", kind="fact", content="At night the disk fills up.")

    assert again == first
    assert paraphrase == "team/alerts/disk@2"
    assert len(store.history(NS, "disk")) == 2


def test_synchronous_put_is_visible_to_list(store: Store) -> None:
    record_id = store.put(NS, "disk", kind="fact", content="The disk fills at night.")
    assert [record.id for record in store.list(NS)] == [record_id]


def test_synchronous_put_is_visible_to_search(store: Store) -> None:
    record_id = store.put(NS, "disk", kind="fact", content="The disk fills at night.")
    assert [hit.record.id for hit in store.search("disk fills at night", [NS])] == [record_id]


def test_buffered_put_is_visible_after_flush(store: Store) -> None:
    with store.writer() as writer:
        writer.put(NS, "disk", kind="fact", content="The disk fills at night.")
        writer.put(NS, "cpu", kind="fact", content="The CPU spikes at noon.")
        assert store.get(NS, "disk") is None
        assert store.list(NS) == []
        assert store.search("disk", [NS]) == []

    assert writer.flushed == ["team/alerts/disk@1", "team/alerts/cpu@1"]
    assert {record.id for record in store.list(NS)} == set(writer.flushed)


def test_last_writer_wins_on_one_key(store: Store, clock: FakeClock) -> None:
    first = store.writer()
    second = store.writer()
    first.put(NS, "disk", kind="fact", content="The disk fills at night.", actor="one")
    second.put(NS, "disk", kind="fact", content="The disk fills at noon.", actor="two")
    first.flush()
    clock.advance()
    second.flush()

    assert [record.actor for record in store.history(NS, "disk")] == ["one", "two"]
    current = store.get(NS, "disk")
    assert current is not None
    assert current.actor == "two"


def test_delete_removes_every_revision(store: Store, clock: FakeClock) -> None:
    store.put(NS, "disk", kind="fact", content="The disk fills at night.")
    clock.advance()
    store.put(NS, "disk", kind="fact", content="The disk fills at noon.")
    store.search("disk fills", [NS])
    store.delete(NS, "disk")

    assert store.get(NS, "disk") is None
    assert store.get(NS, "disk", revision=1) is None
    assert store.history(NS, "disk") == []
    assert store.list(NS) == []
    assert store.search("disk fills at noon", [NS]) == []
    assert store.list_namespaces() == []


FILTER_CASES: list[tuple[str, dict[str, Any], Filter]] = [
    ("kind", {"kind": "procedure"}, Filter(kind="procedure")),
    ("subject", {"subject": "db-1"}, Filter(subject="db-1")),
    ("actor", {"actor": "extractor"}, Filter(actor="extractor")),
    ("tags", {"tags": ("disk", "night")}, Filter(tags=("night", "disk"))),
]


@pytest.mark.parametrize(
    ("name", "fields", "where"), FILTER_CASES, ids=[c[0] for c in FILTER_CASES]
)
def test_filter_matches_by_equality(
    store: Store, name: str, fields: dict[str, Any], where: Filter
) -> None:
    store.put(NS, "skipped", kind="fact", content="This one does not match.", tags=("disk",))
    store.put(NS, "wanted", content="This one matches.", **{"kind": "fact", **fields})
    wanted = store.get(NS, "wanted")
    assert wanted is not None

    assert [record.id for record in store.list(NS, where)] == [wanted.id]
    assert [hit.record.id for hit in store.search("one", [NS], where)] == [wanted.id]


def test_filter_matches_a_time_range(store: Store, clock: FakeClock) -> None:
    start = clock.now
    store.put(NS, "unknown", kind="fact", content="Validity unknown.")
    store.put(NS, "early", kind="fact", content="Valid early.", valid_from=start)
    store.put(NS, "late", kind="fact", content="Valid late.", valid_from=start + timedelta(days=2))
    clock.advance(timedelta(days=1))
    store.put(NS, "newer", kind="fact", content="Written later.")

    early = Filter(valid_from=TimeRange(start=start, end=start + timedelta(days=1)))
    assert [record.key for record in store.list(NS, early)] == ["early"]
    written = Filter(created_at=TimeRange(start=start + timedelta(hours=1)))
    assert [record.key for record in store.list(NS, written)] == ["newer"]
    assert [record.key for record in store.list(NS, since=clock.now)] == ["newer"]


def test_unknown_filter_key_raises() -> None:
    fields: dict[str, Any] = {"content": "disk"}
    with pytest.raises(TypeError):
        Filter(**fields)


def test_prefix_matches_whole_labels(store: Store) -> None:
    store.put(("team", "alerts"), "a", kind="fact", content="Inside the prefix.")
    store.put(("team", "alerts", "disk"), "b", kind="fact", content="Below the prefix.")
    store.put(("team", "alertsx"), "c", kind="fact", content="Shares a string prefix.")

    assert {record.key for record in store.list(("team", "alerts"))} == {"a", "b"}
    found = store.search("prefix", [("team", "alerts")])
    assert {hit.record.key for hit in found} == {"a", "b"}
    both = store.search(None, [("team", "alerts", "disk"), ("team", "alertsx")])
    assert {hit.record.key for hit in both} == {"b", "c"}


def test_search_returns_distance_closest_first(store: Store, clock: FakeClock) -> None:
    store.put(NS, "exact", kind="fact", content="disk fills at night")
    clock.advance()
    store.put(NS, "near", kind="fact", content="disk fills at noon")
    clock.advance()
    store.put(NS, "far", kind="fact", content="cpu spikes after deploy")

    hits = store.search("disk fills at night", [NS], k=3)
    assert [hit.record.key for hit in hits] == ["exact", "near", "far"]
    distances = [hit.distance for hit in hits]
    assert distances[0] == pytest.approx(0.0)
    assert all(distance is not None for distance in distances)
    assert distances == sorted(distances, key=lambda distance: distance or 0.0)
    assert len(store.search("disk fills at night", [NS], k=2)) == 2


def test_forget_after_hides_without_deleting(store: Store, clock: FakeClock) -> None:
    store.put(
        NS,
        "disk",
        kind="fact",
        content="The disk fills at night.",
        forget_after=clock.now + timedelta(days=1),
        forget_reason="temporary",
    )
    assert len(store.list(NS)) == 1
    clock.advance(timedelta(days=1))

    assert store.list(NS) == []
    assert store.search("disk", [NS]) == []
    assert store.get(NS, "disk") is not None
    assert len(store.history(NS, "disk")) == 1


def test_put_after_forget_after_writes_a_new_revision(store: Store, clock: FakeClock) -> None:
    store.put(
        NS,
        "disk",
        kind="fact",
        content="The disk fills at night.",
        forget_after=clock.now + timedelta(days=1),
        forget_reason="temporary",
    )
    clock.advance(timedelta(days=1))
    record_id = store.put(NS, "disk", kind="fact", content="The disk fills at night.")

    assert record_id == "team/alerts/disk@2"
    assert [record.id for record in store.list(NS)] == [record_id]
    assert [hit.record.id for hit in store.search("disk fills at night", [NS])] == [record_id]


def test_stored_record_cannot_change(store: Store) -> None:
    payload: dict[str, JSONValue] = {"hosts": ["db-1"], "limits": {"disk": 90}}
    store.put(NS, "disk", kind="fact", content="The disk fills at night.", payload=payload)
    expected = {"hosts": ["db-1"], "limits": {"disk": 90}}

    payload["hosts"] = ["changed"]
    returned = [
        store.get(NS, "disk"),
        store.get(NS, "disk", revision=1),
        *store.history(NS, "disk"),
        *store.list(NS),
        *(hit.record for hit in store.search(None, [NS])),
        *(hit.record for hit in store.search("disk", [NS])),
    ]
    for record in returned:
        assert record is not None
        assert record.payload == expected
        hosts = record.payload["hosts"]
        assert isinstance(hosts, list)
        hosts.append("db-2")
        record.payload["new"] = True

    stored = store.get(NS, "disk")
    assert stored is not None
    assert stored.payload == expected


@pytest.mark.parametrize("content", ["", "  \n\t "])
def test_put_refuses_empty_content(store: Store, content: str) -> None:
    with pytest.raises(ValueError, match="content"):
        store.put(NS, "disk", kind="fact", content=content)
    with pytest.raises(ValueError, match="content"), store.writer() as writer:
        writer.put(NS, "disk", kind="fact", content=content)

    assert store.get(NS, "disk") is None
    assert store.list_namespaces() == []


def test_key_cannot_cross_the_episode_line(store: Store, clock: FakeClock) -> None:
    store.put(NS, "thread", kind="episode", content="First chunk of the thread.")
    store.put(NS, "disk", kind="fact", content="The disk fills at night.")
    clock.advance()

    with pytest.raises(ValueError, match="episode"):
        store.put(NS, "thread", kind="fact", content="A fact under an episode key.")
    with pytest.raises(ValueError, match="episode"):
        store.put(NS, "disk", kind="episode", content="An episode under a fact key.")
    profile = store.put(NS, "disk", kind="profile", content="The disk is the bottleneck.")

    assert [record.kind for record in store.history(NS, "thread")] == ["episode"]
    assert [record.kind for record in store.history(NS, "disk")] == ["fact", "profile"]
    assert profile == "team/alerts/disk@2"


def test_filtered_search_finds_every_later_write(store: Store, clock: FakeClock) -> None:
    keys = ["first", "second", "third", "fourth"]
    for key in keys:
        store.put(NS, key, kind="fact", content=f"The disk fills, {key} report.")
        store.put(NS, f"{key}-cpu", kind="procedure", content=f"Restart the CPU, {key} step.")
        clock.advance()

    hits = store.search("disk fills report", [NS], Filter(kind="fact"), k=10)
    assert sorted(hit.record.key for hit in hits) == sorted(keys)
    nothing = store.search("disk fills report", [("other",)], Filter(kind="fact"), k=10)
    assert nothing == []


def test_sweep_deletes_keys_past_forget_after(store: Store, clock: FakeClock) -> None:
    soon = clock.now + timedelta(days=1)
    store.put(NS, "gone", kind="fact", content="Disk fills at night.")
    clock.advance()
    store.put(
        NS,
        "gone",
        kind="fact",
        content="Disk fills at noon.",
        cues=("when does the disk fill?",),
        forget_after=soon,
        forget_reason="temporary",
    )
    store.put(NS, "back", kind="fact", content="Comes back.", forget_after=soon)
    store.put(NS, "kept", kind="fact", content="Kept.", forget_after=soon + timedelta(days=1))
    store.put(NS, "plain", kind="fact", content="No forget_after.")
    store.put(NS, "chunk", kind="episode", content="Old chunk.", forget_after=soon)
    clock.advance(timedelta(days=1))
    store.put(NS, "back", kind="fact", content="Comes back.")

    assert store.sweep() == ["team/alerts/chunk@1", "team/alerts/gone@1", "team/alerts/gone@2"]
    assert store.history(NS, "gone") == []
    assert store.history(NS, "chunk") == []
    assert [record.revision for record in store.history(NS, "back")] == [1, 2]
    assert {record.key for record in store.list(NS)} == {"back", "kept", "plain"}
    assert all(hit.record.key != "gone" for hit in store.search("disk fill", [NS], k=10))
    assert store.sweep() == []
