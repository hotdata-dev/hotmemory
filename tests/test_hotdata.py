"""Tests of `HotdataStore` that need a running engine. They skip without HOTMEMORY_TEST_URL."""

import os
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from hotdata_framework import HotdataClient

from hotmemory import Filter
from hotmemory.hotdata import (
    CUE_TABLE,
    EPISODE_TABLE,
    MEMORY_TABLE,
    META_TABLE,
    RECORD_SCHEMA,
    HotdataStore,
    LayoutError,
    _record,
)

from .conftest import DIMENSIONS, FakeClock, fake_embedder

pytestmark = pytest.mark.hotdata

MODEL = "fake-crc32"


@pytest.fixture
def client() -> Iterator[HotdataClient]:
    url = os.environ.get("HOTMEMORY_TEST_URL")
    if not url:
        pytest.skip("HOTMEMORY_TEST_URL is not set")
    api_key = os.environ.get("HOTDATA_API_KEY") or "local"
    workspace = os.environ.get("HOTDATA_WORKSPACE") or "local"
    with HotdataClient(api_key, workspace, host=url) as client:
        yield client


@pytest.fixture
def name(client: HotdataClient) -> Iterator[str]:
    """A new database name. Every database with this name is deleted after the test."""
    name = f"hotmemory-test-{uuid.uuid4().hex[:12]}"
    yield name
    for database in client.list_managed_databases():
        if database.description == name:
            client.delete_managed_database(database)


def named(client: HotdataClient, name: str) -> list[str]:
    return sorted(db.id for db in client.list_managed_databases() if db.description == name)


def provision(client: HotdataClient, name: str, model: str = MODEL) -> HotdataStore:
    return HotdataStore.provision(
        name, embedder=fake_embedder, model=model, dimensions=DIMENSIONS, client=client
    )


def test_provision_again_opens_the_same_database(client: HotdataClient, name: str) -> None:
    first = provision(client, name)
    second = provision(client, name)

    assert second.database == first.database
    assert named(client, name) == [first.database.id]


def test_provision_leaves_empty_tables_and_one_meta_row(client: HotdataClient, name: str) -> None:
    store = provision(client, name)

    for table in (MEMORY_TABLE, EPISODE_TABLE, CUE_TABLE):
        sql = f"SELECT count(*) FROM default.public.{table}"
        assert client.execute_sql(sql, database=store.database).rows == [[0]]
    meta = client.execute_sql(
        f"SELECT schema_version, model, dimensions FROM default.public.{META_TABLE}",
        database=store.database,
    )
    assert meta.rows == [[1, MODEL, DIMENSIONS]]


def test_provision_refuses_a_duplicate_name(client: HotdataClient, name: str) -> None:
    client.create_managed_database(name)
    client.create_managed_database(name)
    before = named(client, name)

    with pytest.raises(LayoutError, match="2 databases"):
        provision(client, name)
    assert named(client, name) == before


def test_provision_refuses_another_layout(client: HotdataClient, name: str) -> None:
    client.create_managed_database(name, tables=[MEMORY_TABLE], keys={MEMORY_TABLE: ["id"]})
    before = named(client, name)

    with pytest.raises(LayoutError, match="no table"):
        provision(client, name)
    assert named(client, name) == before


def test_provision_refuses_another_embedding_model(client: HotdataClient, name: str) -> None:
    store = provision(client, name)

    with pytest.raises(LayoutError, match="model"):
        provision(client, name, model="another-model")
    with pytest.raises(LayoutError, match="dimensions"):
        HotdataStore.open(
            store.database.id,
            embedder=fake_embedder,
            model=MODEL,
            dimensions=DIMENSIONS + 1,
            client=client,
        )
    assert named(client, name) == [store.database.id]


def test_open_by_id(client: HotdataClient, name: str) -> None:
    store = provision(client, name)
    opened = HotdataStore.open(
        store.database.id, embedder=fake_embedder, model=MODEL, dimensions=DIMENSIONS, client=client
    )

    assert opened.database == store.database
    with pytest.raises(KeyError):
        HotdataStore.open(
            name, embedder=fake_embedder, model=MODEL, dimensions=DIMENSIONS, client=client
        )


@pytest.fixture
def store(client: HotdataClient, name: str) -> HotdataStore:
    return HotdataStore.provision(
        name,
        embedder=fake_embedder,
        model=MODEL,
        dimensions=DIMENSIONS,
        client=client,
        clock=FakeClock(),
    )


def rows(store: HotdataStore, table: str, columns: str = "id, superseded_by") -> list[list[Any]]:
    sql = f"SELECT {columns} FROM default.public.{table} ORDER BY id"
    return list(store.client.execute_sql(sql, database=store.database).rows)


NS = ("team", "alerts")


def test_put_supersedes_the_current_row(store: HotdataStore) -> None:
    store.put(NS, "disk", kind="fact", content="The disk fills at night.", cues=("disk full?",))
    store.put(NS, "disk", kind="fact", content="The disk fills at noon.")
    again = store.put(NS, "disk", kind="fact", content="the DISK fills  at noon.")

    assert again == "team/alerts/disk@2"
    assert rows(store, MEMORY_TABLE) == [
        ["team/alerts/disk@1", "team/alerts/disk@2"],
        ["team/alerts/disk@2", None],
    ]
    assert rows(store, CUE_TABLE, "id") == [["team/alerts/disk@1"]]


def test_put_keeps_every_field(store: HotdataStore) -> None:
    observed = datetime(2025, 3, 4, 5, 6, 7, 891011, tzinfo=UTC)
    store.put(
        NS,
        "disk",
        kind="fact",
        content="The disk fills at night.",
        subject="db-1",
        cues=("why is the disk full?", "when does it fill?"),
        payload={"hosts": ["db-1"], "limit": 0.9, "quote": "it's"},
        tags=("disk", "night"),
        sources=("thread-1",),
        actor="extractor",
        observed_at=observed,
        valid_until=observed + timedelta(days=1),
        forget_after=observed + timedelta(days=30),
        forget_reason="temporary",
    )
    store.put(NS, "disk", kind="fact", content="The disk fills at noon.")

    current, vectors = store._current({(NS, "disk")})
    record = current[(NS, "disk")]
    assert record.id == "team/alerts/disk@2"
    columns = ", ".join(RECORD_SCHEMA.names)
    [row] = store.client.execute_sql(
        f"SELECT {columns} FROM default.public.{MEMORY_TABLE} WHERE revision = 1",
        database=store.database,
    ).to_records()
    first = _record(row)
    assert first.cues == ("why is the disk full?", "when does it fill?")
    assert first.payload == {"hosts": ["db-1"], "limit": 0.9, "quote": "it's"}
    assert (first.subject, first.tags, first.sources, first.actor) == (
        "db-1",
        ("disk", "night"),
        ("thread-1",),
        "extractor",
    )
    assert (first.observed_at, first.valid_from) == (observed, observed)
    assert first.valid_until == observed + timedelta(days=1)
    assert first.forget_after == observed + timedelta(days=30)
    assert first.superseded_by == "team/alerts/disk@2"
    assert len(row["content_embedding"]) == DIMENSIONS
    assert vectors[record.id] == pytest.approx(fake_embedder([record.content])[0])


def test_episode_goes_to_its_own_table(store: HotdataStore) -> None:
    store.put(NS, "thread", kind="episode", content="First chunk.", cues=("what happened?",))
    store.put(NS, "disk", kind="fact", content="The disk fills at night.")

    assert rows(store, EPISODE_TABLE) == [["team/alerts/thread@1", None]]
    assert rows(store, MEMORY_TABLE) == [["team/alerts/disk@1", None]]
    assert rows(store, CUE_TABLE, "id") == [["team/alerts/thread@1"]]
    with pytest.raises(ValueError, match="episode"):
        store.put(NS, "thread", kind="fact", content="Not an episode.")


def test_writer_sends_one_load_per_table(store: HotdataStore) -> None:
    loads: list[str] = []
    original = store._load

    def counting(table: str, rows: Any, mode: Any) -> None:
        loads.append(table)
        original(table, rows, mode)

    store._load = counting  # type: ignore[method-assign]
    with store.writer() as writer:
        writer.put(NS, "a", kind="fact", content="One.", cues=("one?",))
        writer.put(NS, "b", kind="fact", content="Two.")
        writer.put(NS, "a", kind="fact", content="One again.")
        writer.put(NS, "t", kind="episode", content="Chunk.")

    assert writer.flushed == [
        "team/alerts/a@1",
        "team/alerts/b@1",
        "team/alerts/a@2",
        "team/alerts/t@1",
    ]
    assert sorted(loads) == [CUE_TABLE, EPISODE_TABLE, MEMORY_TABLE]
    assert rows(store, MEMORY_TABLE) == [
        ["team/alerts/a@1", "team/alerts/a@2"],
        ["team/alerts/a@2", None],
        ["team/alerts/b@1", None],
    ]


def test_delete_removes_rows_and_cues(store: HotdataStore) -> None:
    store.put(NS, "disk", kind="fact", content="The disk fills at night.", cues=("full?",))
    store.put(NS, "disk", kind="fact", content="The disk fills at noon.", cues=("noon?",))
    store.put(NS, "cpu", kind="fact", content="The CPU spikes.", cues=("cpu?",))
    store.delete(NS, "disk")
    store.delete(NS, "missing")

    assert rows(store, MEMORY_TABLE) == [["team/alerts/cpu@1", None]]
    assert rows(store, CUE_TABLE, "id") == [["team/alerts/cpu@1"]]


def test_embedder_of_another_size_writes_nothing(client: HotdataClient, name: str) -> None:
    store = HotdataStore.provision(
        name,
        embedder=lambda texts: [[1.0] * 3 for _ in texts],
        model=MODEL,
        dimensions=DIMENSIONS,
        client=client,
    )
    with pytest.raises(ValueError, match="dimensions|floats"):
        store.put(NS, "disk", kind="fact", content="The disk fills at night.")
    assert rows(store, MEMORY_TABLE) == []


def test_vector_ranking_matches_by_content_alone(client: HotdataClient, name: str) -> None:
    store = HotdataStore.provision(
        name, embedder=fake_embedder, model=MODEL, dimensions=DIMENSIONS, client=client
    )
    vector = HotdataStore(
        client,
        store.database,
        embedder=fake_embedder,
        model=MODEL,
        dimensions=DIMENSIONS,
        ranking="vector",
    )
    for key in ("one", "two", "three"):
        vector.put(NS, key, kind="fact", content=f"disk report {key}")
        vector.put(NS, f"{key}-x", kind="procedure", content=f"disk report {key}")

    hits = vector.search("disk report two", [NS], Filter(kind="fact"), k=10)
    assert [hit.record.key for hit in hits][:1] == ["two"]
    assert sorted(hit.record.key for hit in hits) == ["one", "three", "two"]
    distances = [hit.distance for hit in hits]
    assert distances == sorted(distances, key=lambda distance: distance or 0.0)


@pytest.mark.parametrize("ranking", ["fused", "vector"])
def test_search_in_a_narrow_scope_returns_its_top_k(
    client: HotdataClient, name: str, ranking: str
) -> None:
    store = provision(client, name)
    store._ranking = ranking  # type: ignore[assignment]
    narrow = ("team", "narrow")
    with store.writer() as writer:
        for n in range(990):
            writer.put(NS, f"wide-{n}", kind="fact", content=f"disk report {n}", cues=("disk?",))
        for n in range(10):
            writer.put(narrow, f"narrow-{n}", kind="fact", content=f"cpu load {n}", cues=("cpu?",))

    hits = store.search("disk report", [narrow], k=10)
    assert sorted(hit.record.key for hit in hits) == sorted(f"narrow-{n}" for n in range(10))


def test_two_stores_write_one_database_at_once(client: HotdataClient, name: str) -> None:
    first = provision(client, name)
    second = HotdataStore.open(
        first.database.id, embedder=fake_embedder, model=MODEL, dimensions=DIMENSIONS, client=client
    )
    retries: list[float] = []

    def sleep(seconds: float) -> None:
        retries.append(seconds)
        time.sleep(seconds)

    for store in (first, second):
        store._sleep = sleep

    def write(store: HotdataStore, prefix: str) -> None:
        for n in range(6):
            store.put(NS, f"{prefix}{n}", kind="fact", content=f"Fact {n} from {prefix}.")

    threads = [threading.Thread(target=write, args=args) for args in ((first, "a"), (second, "b"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    keys = {record.key for record in first.list(NS)}
    assert keys == {f"{prefix}{n}" for prefix in "ab" for n in range(6)}
    assert all(seconds <= 4.0 for seconds in retries)


def test_one_store_serializes_writes_on_one_key(store: HotdataStore) -> None:
    def write(writer: str) -> None:
        for n in range(4):
            store.put(NS, "disk", kind="fact", content=f"Revision {n} from {writer}.")

    threads = [threading.Thread(target=write, args=(writer,)) for writer in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    history = store.history(NS, "disk")
    assert [record.revision for record in history] == list(range(1, 9))
    assert [record.superseded_by is None for record in history] == [False] * 7 + [True]


def test_query_with_no_words_ranks_by_vector(client: HotdataClient, name: str) -> None:
    def embed(texts: Any) -> list[list[float]]:
        return [[1.0] + [0.0] * (DIMENSIONS - 1) for _ in texts]

    store = HotdataStore.provision(
        name, embedder=embed, model=MODEL, dimensions=DIMENSIONS, client=client
    )
    store.put(NS, "disk", kind="fact", content="The disk fills at night.")

    for query in ("?", "--", "   "):
        assert [hit.record.key for hit in store.search(query, [NS])] == ["disk"]
