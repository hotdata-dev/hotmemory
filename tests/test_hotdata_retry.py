"""Offline tests of the load retry in `HotdataStore`, with a fake client."""

from datetime import UTC, datetime
from typing import Any

import pyarrow as pa
import pytest
from hotdata_framework import ManagedDatabase

from hotmemory.hotdata import (
    INSERT_ROWS,
    LOAD_ATTEMPTS,
    MEMORY_TABLE,
    HotdataStore,
    _insert_sql,
    _literal,
)

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


class FakeSqlClient(FakeClient):
    """Refuses the first `sql_refusals` statements with `status` and `body`, then runs them."""

    def __init__(self, sql_refusals: int, status: int = 409, body: str = LOCKED) -> None:
        super().__init__(refusals=0)
        self.sql_refusals = sql_refusals
        self.sql_status = status
        self.sql_body = body
        self.statements: list[str] = []

    def execute_sql(self, sql: str, database: Any = None) -> Any:
        if sql.startswith("SELECT"):
            return Empty()
        self.statements.append(sql)
        if len(self.statements) <= self.sql_refusals:
            raise RuntimeError("Conflict") from Refusal(self.sql_status, self.sql_body)
        return None


class Empty:
    rows: list[Any] = []

    def to_records(self) -> list[Any]:
        return []


INSERTED = pa.table({"id": ["team/disk@1", "team/cpu@1"], "count": [3, None]})


def test_insert_sql_writes_only_the_ids_that_the_table_lacks() -> None:
    sql = _insert_sql(MEMORY_TABLE, INSERTED)
    assert sql.startswith(f"INSERT INTO default.public.{MEMORY_TABLE} (id, count) SELECT * FROM")
    assert "VALUES ('team/disk@1', 3), ('team/cpu@1', NULL)" in sql
    assert sql.endswith("WHERE id IN ('team/disk@1', 'team/cpu@1'))")
    assert "new.id NOT IN (SELECT id FROM" in sql


def test_literal_of_each_column_type() -> None:
    texts = pa.list_(pa.string())
    vector = pa.list_(pa.float32())
    time = pa.timestamp("us", tz="UTC")
    assert _literal(["a", "it's"], texts) == "ARRAY['a', 'it''s']"
    assert _literal([], texts) == "CAST(ARRAY[] AS VARCHAR[])"
    assert _literal([0.5, 1.0], vector) == "CAST(ARRAY[0.5, 1.0] AS REAL[])"
    when = datetime(2026, 10, 21, 8, 0, 1, 5, tzinfo=UTC)
    assert _literal(when, time) == "TIMESTAMP '2026-10-21T08:00:01.000005Z'"
    assert _literal(None, time) == "NULL"
    assert _literal("{}", pa.string()) == "'{}'"


def test_a_locked_insert_retries_and_never_uploads() -> None:
    client = FakeSqlClient(sql_refusals=2)
    sleeps: list[float] = []
    store(client, sleeps)._load(MEMORY_TABLE, ROWS, "insert")
    assert len(client.statements) == 3
    assert sleeps == [0.25, 0.5]
    assert client.uploads == 0


def test_a_refused_insert_falls_back_to_the_load_from_then_on() -> None:
    client = FakeSqlClient(sql_refusals=1, status=400, body="Bad Request")
    sleeps: list[float] = []
    target = store(client, sleeps)
    target._load(MEMORY_TABLE, ROWS, "insert")
    target._load(MEMORY_TABLE, ROWS, "insert")
    assert len(client.statements) == 1
    assert client.loads == ["upload-1", "upload-2"]


def test_a_small_write_of_new_rows_inserts_and_a_large_one_loads() -> None:
    client = FakeSqlClient(sql_refusals=0)
    target = store(client, [])
    target.put(("team",), "disk", kind="fact", content="disk full", cues=("disk?",))
    assert len(client.statements) == 2
    assert client.uploads == 0
    with target.writer(max_rows=100) as writer:
        for n in range(INSERT_ROWS + 1):
            writer.put(("team",), f"key-{n}", kind="fact", content=f"fact {n}")
    assert len(client.statements) == 2
    assert client.uploads == 1
