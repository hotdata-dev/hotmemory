from datetime import timedelta

import pytest

from hotmemory import MemoryStore
from hotmemory._rules import cosine_distance

from .conftest import FakeClock, fake_embedder

NS = ("team",)


def test_search_with_query_text_needs_an_embedder() -> None:
    store = MemoryStore()
    store.put(NS, "a", kind="fact", content="disk full")
    with pytest.raises(RuntimeError, match="embedder"):
        store.search("disk", [NS])


def test_search_without_query_text_needs_no_embedder() -> None:
    store = MemoryStore()
    store.put(NS, "a", kind="fact", content="disk full")
    assert [hit.distance for hit in store.search(None, [NS])] == [None]


def test_embedder_that_drops_a_vector_raises() -> None:
    store = MemoryStore(embedder=lambda texts: [])
    store.put(NS, "a", kind="fact", content="disk full")
    with pytest.raises(ValueError, match="vectors"):
        store.search("disk", [NS])


def test_created_at_comes_from_the_clock(clock: FakeClock) -> None:
    store = MemoryStore(clock=clock)
    store.put(NS, "a", kind="fact", content="disk full")
    record = store.get(NS, "a")
    assert record is not None
    assert record.created_at == clock.now


def test_valid_from_defaults_to_observed_at(clock: FakeClock) -> None:
    store = MemoryStore(clock=clock)
    observed = clock.now - timedelta(days=365)
    store.put(NS, "a", kind="fact", content="disk full", observed_at=observed)
    record = store.get(NS, "a")
    assert record is not None
    assert record.valid_from == observed


def test_writer_flushes_at_its_row_count(clock: FakeClock) -> None:
    store = MemoryStore(clock=clock)
    writer = store.writer(max_rows=2)
    writer.put(NS, "a", kind="fact", content="one")
    assert store.get(NS, "a") is None
    writer.put(NS, "b", kind="fact", content="two")
    assert writer.flushed == ["team/a@1", "team/b@1"]


def test_writer_flushes_on_the_first_put_after_its_interval(clock: FakeClock) -> None:
    store = MemoryStore(clock=clock)
    writer = store.writer(interval=timedelta(seconds=5))
    writer.put(NS, "a", kind="fact", content="one")
    clock.advance(timedelta(seconds=5))
    writer.put(NS, "b", kind="fact", content="two")
    assert writer.flushed == ["team/a@1", "team/b@1"]


def test_writer_drops_its_buffer_when_the_block_raises(clock: FakeClock) -> None:
    store = MemoryStore(clock=clock)
    with pytest.raises(KeyError), store.writer() as writer:
        writer.put(NS, "a", kind="fact", content="one")
        raise KeyError("stop")
    assert store.get(NS, "a") is None
    assert writer.flushed == []


def test_writer_checks_a_put_before_it_buffers() -> None:
    writer = MemoryStore().writer()
    with pytest.raises(ValueError, match="label"):
        writer.put(("a.b",), "k", kind="fact", content="one")


def test_list_namespaces_matches_whole_labels() -> None:
    store = MemoryStore()
    store.put(("team", "alerts"), "a", kind="fact", content="one")
    store.put(("team", "alertsx"), "b", kind="fact", content="two")
    store.put(("other",), "c", kind="fact", content="three")
    assert store.list_namespaces(("team", "alerts")) == [("team", "alerts")]
    assert store.list_namespaces() == [("other",), ("team", "alerts"), ("team", "alertsx")]


def test_reput_after_delete_does_not_reuse_a_stale_embedding() -> None:
    store = MemoryStore(embedder=fake_embedder)
    store.put(NS, "a", kind="fact", content="disk full")
    store.search("disk full", [NS])
    store.delete(NS, "a")
    store.put(NS, "a", kind="fact", content="network down")
    [hit] = store.search("network down", [NS])
    assert hit.distance == pytest.approx(0.0)


def test_cosine_distance() -> None:
    assert cosine_distance([1.0, 0.0], [1.0, 0.0]) == pytest.approx(0.0)
    assert cosine_distance([1.0, 0.0], [0.0, 1.0]) == pytest.approx(1.0)
    assert cosine_distance([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(2.0)


@pytest.mark.parametrize(("a", "b"), [([1.0], [1.0, 0.0]), ([0.0], [1.0])])
def test_cosine_distance_refuses_bad_vectors(a: list[float], b: list[float]) -> None:
    with pytest.raises(ValueError):
        cosine_distance(a, b)
