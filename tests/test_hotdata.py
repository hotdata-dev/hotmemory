"""Tests of `HotdataStore` that need a running engine. They skip without HOTMEMORY_TEST_URL."""

import os
import uuid
from collections.abc import Iterator

import pytest
from hotdata_framework import HotdataClient

from hotmemory.hotdata import (
    CUE_TABLE,
    EPISODE_TABLE,
    MEMORY_TABLE,
    META_TABLE,
    HotdataStore,
    LayoutError,
)

from .conftest import DIMENSIONS, fake_embedder

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
