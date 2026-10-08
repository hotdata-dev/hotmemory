import os
import re
import uuid
import zlib
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pytest
from hotdata_framework import HotdataClient, ManagedDatabase

from hotmemory import Clock, Embedder, MemoryStore, Store
from hotmemory.hotdata import CUE_TABLE, RECORD_TABLES, HotdataStore

START = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
DIMENSIONS = 64
MODEL = "fake-crc32"


class FakeClock:
    """A clock that stands still until a test moves it."""

    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta = timedelta(seconds=1)) -> None:
        self.now += delta


def fake_embedder(texts: Sequence[str]) -> list[list[float]]:
    """Embed each text as counts of its words, hashed into a fixed number of dimensions."""
    vectors = []
    for text in texts:
        vector = [0.0] * DIMENSIONS
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[zlib.crc32(word.encode()) % DIMENSIONS] += 1.0
        vectors.append(vector)
    return vectors


StoreFactory = Callable[[pytest.FixtureRequest, Embedder | None, Clock], Store]


def memory_store(request: pytest.FixtureRequest, embedder: Embedder | None, clock: Clock) -> Store:
    return MemoryStore(embedder=embedder, clock=clock)


def hotdata_store(request: pytest.FixtureRequest, embedder: Embedder | None, clock: Clock) -> Store:
    """Return a store over the database of this run, and empty its tables after the test."""
    client, database = request.getfixturevalue("hotdata_database")
    assert embedder is not None
    store = HotdataStore(
        client, database, embedder=embedder, model=MODEL, dimensions=DIMENSIONS, clock=clock
    )
    request.addfinalizer(lambda: empty(store))
    return store


def empty(store: HotdataStore) -> None:
    """Delete every row from the record tables and the cue table of `store`."""
    for table in (*RECORD_TABLES, CUE_TABLE):
        sql = f"SELECT id FROM default.public.{table}"
        ids = [row[0] for row in store.client.execute_sql(sql, database=store.database).rows]
        if ids:
            store._load(table, pa.table({"id": ids}), "delete")


DRIVERS: dict[str, StoreFactory] = {
    "memory": memory_store,
    "hotdata": hotdata_store,
}
MARKS = {"hotdata": pytest.mark.hotdata}


@pytest.fixture(scope="session")
def hotdata_client() -> Iterator[HotdataClient]:
    """A client for the engine at HOTMEMORY_TEST_URL. Skips the test when it is not set."""
    url = os.environ.get("HOTMEMORY_TEST_URL")
    if not url:
        pytest.skip("HOTMEMORY_TEST_URL is not set")
    api_key = os.environ.get("HOTDATA_API_KEY") or "local"
    workspace = os.environ.get("HOTDATA_WORKSPACE") or "local"
    with HotdataClient(api_key, workspace, host=url) as client:
        yield client


@pytest.fixture(scope="session")
def hotdata_database(
    hotdata_client: HotdataClient,
) -> Iterator[tuple[HotdataClient, ManagedDatabase]]:
    """A database with a new name, provisioned once for the run and deleted at its end."""
    store = HotdataStore.provision(
        f"hotmemory-test-{uuid.uuid4().hex[:12]}",
        embedder=fake_embedder,
        model=MODEL,
        dimensions=DIMENSIONS,
        client=hotdata_client,
    )
    yield hotdata_client, store.database
    hotdata_client.delete_managed_database(store.database)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(params=[pytest.param(name, marks=MARKS.get(name, ())) for name in sorted(DRIVERS)])
def store(request: pytest.FixtureRequest, clock: FakeClock) -> Store:
    factory: StoreFactory = DRIVERS[request.param]
    return factory(request, fake_embedder, clock)
