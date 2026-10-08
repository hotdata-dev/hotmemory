"""Offline tests of the load retry in `HotdataStore`, with a fake client."""

from typing import Any

import pyarrow as pa
import pytest
from hotdata_framework import ManagedDatabase

from hotmemory.hotdata import LOAD_ATTEMPTS, MEMORY_TABLE, HotdataStore

from .conftest import DIMENSIONS, FakeClock, fake_embedder

LOCKED = '{"error":{"message":"another operation is already running","code":"RESOURCE_LOCKED"}}'


class Refusal(Exception):
    """Stands in for the API exception that the framework chains to its RuntimeError."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(body)
        self.status = status
        self.body = body


class FakeClient:
    """Refuses the first `refusals` loads with `status` and `body`, then accepts loads."""

    def __init__(self, refusals: int, status: int = 409, body: str = LOCKED) -> None:
        self.refusals = refusals
        self.status = status
        self.body = body
        self.uploads = 0
        self.loads: list[str] = []

    def upload_parquet(self, path: str) -> str:
        self.uploads += 1
        return f"upload-{self.uploads}"

    def load_managed_table(self, database: Any, table: str, **kwargs: Any) -> None:
        self.loads.append(kwargs["upload_id"])
        if len(self.loads) <= self.refusals:
            raise RuntimeError("Conflict") from Refusal(self.status, self.body)


def store(client: FakeClient, sleeps: list[float]) -> HotdataStore:
    database = ManagedDatabase(id="db", description="test", default_connection_id="conn")
    result = HotdataStore(
        client,  # type: ignore[arg-type]
        database,
        embedder=fake_embedder,
        model="fake",
        dimensions=DIMENSIONS,
        clock=FakeClock(),
    )
    result._sleep = sleeps.append
    return result


ROWS = pa.table({"id": ["team/disk@1"]})


def test_a_locked_load_retries_with_a_doubling_backoff() -> None:
    client = FakeClient(refusals=LOAD_ATTEMPTS - 1)
    sleeps: list[float] = []
    store(client, sleeps)._load(MEMORY_TABLE, ROWS, "upsert")

    assert sleeps == [0.25, 0.5, 1.0, 2.0, 4.0, 4.0, 4.0]
    assert client.uploads == 1
    assert client.loads == ["upload-1"] * LOAD_ATTEMPTS


def test_a_load_stops_after_the_last_attempt() -> None:
    client = FakeClient(refusals=LOAD_ATTEMPTS)
    sleeps: list[float] = []
    with pytest.raises(RuntimeError, match="Conflict"):
        store(client, sleeps)._load(MEMORY_TABLE, ROWS, "upsert")
    assert len(client.loads) == LOAD_ATTEMPTS
    assert len(sleeps) == LOAD_ATTEMPTS - 1


@pytest.mark.parametrize(
    ("status", "body"), [(409, '{"error":{"code":"CONFLICT"}}'), (400, "Bad Request")]
)
def test_another_refusal_is_not_retried(status: int, body: str) -> None:
    client = FakeClient(refusals=1, status=status, body=body)
    sleeps: list[float] = []
    with pytest.raises(RuntimeError):
        store(client, sleeps)._load(MEMORY_TABLE, ROWS, "upsert")
    assert len(client.loads) == 1
    assert sleeps == []
