"""`HotdataStore`: the driver that keeps records in one Hotdata managed database.

Needs the `hotdata` extra: `pip install hotmemory[hotdata]`.
"""

from __future__ import annotations

import builtins
import json
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from hotdata.api.indexes_api import IndexesApi
from hotdata_framework import HotdataClient, ManagedDatabase
from hotdata_framework.client import IndexType, ManagedLoadMode, VectorMetric

from hotmemory._rules import (
    build_record,
    check_episode_line,
    is_duplicate,
    next_revision,
)
from hotmemory._writer import BufferedWriter
from hotmemory.memory import utc_now
from hotmemory.record import (
    SCHEMA_VERSION,
    JSONValue,
    Kind,
    Record,
    check_key,
    check_namespace,
)
from hotmemory.store import Clock, Embedder

SCHEMA = "public"
MEMORY_TABLE = f"memory_v{SCHEMA_VERSION}"
EPISODE_TABLE = f"episode_v{SCHEMA_VERSION}"
CUE_TABLE = f"cue_v{SCHEMA_VERSION}"
META_TABLE = f"meta_v{SCHEMA_VERSION}"
TABLES = (MEMORY_TABLE, EPISODE_TABLE, CUE_TABLE, META_TABLE)
RECORD_TABLES = (MEMORY_TABLE, EPISODE_TABLE)
SEED_ID = "seed"
META_ID = "meta"
LOAD_ATTEMPTS = 8
FIRST_BACKOFF = 0.25
MAX_BACKOFF = 4.0

_TIME = pa.timestamp("us", tz="UTC")
_TEXTS = pa.list_(pa.string())
_VECTOR = pa.list_(pa.float32())

RECORD_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("namespace", pa.string()),
        ("key", pa.string()),
        ("revision", pa.int64()),
        ("kind", pa.string()),
        ("subject", pa.string()),
        ("content", pa.string()),
        ("cues", _TEXTS),
        ("payload", pa.string()),
        ("tags", _TEXTS),
        ("sources", _TEXTS),
        ("actor", pa.string()),
        ("created_at", _TIME),
        ("observed_at", _TIME),
        ("valid_from", _TIME),
        ("valid_until", _TIME),
        ("expired_at", _TIME),
        ("superseded_by", pa.string()),
        ("forget_after", _TIME),
        ("forget_reason", pa.string()),
        ("content_embedding", _VECTOR),
    ]
)
_Load = tuple[str, pa.Table, ManagedLoadMode]
"""One load: the table, the rows, and the load mode."""

CUE_SCHEMA = pa.schema([("id", pa.string()), ("cues_embedding", _VECTOR)])
META_SCHEMA = pa.schema(
    [
        ("id", pa.string()),
        ("schema_version", pa.int64()),
        ("model", pa.string()),
        ("dimensions", pa.int64()),
    ]
)
SCHEMAS = {
    MEMORY_TABLE: RECORD_SCHEMA,
    EPISODE_TABLE: RECORD_SCHEMA,
    CUE_TABLE: CUE_SCHEMA,
    META_TABLE: META_SCHEMA,
}


@dataclass(frozen=True)
class _Index:
    """One index that `provision` builds: its type, its column, and its metric."""

    table: str
    index_type: IndexType
    column: str
    metric: VectorMetric | None = None


INDEXES = (
    *(_Index(table, "bm25", "content") for table in RECORD_TABLES),
    *(_Index(table, "vector", "content_embedding", "cosine") for table in RECORD_TABLES),
    *(_Index(table, "sorted", "created_at") for table in RECORD_TABLES),
    _Index(CUE_TABLE, "vector", "cues_embedding", "cosine"),
)


class LayoutError(RuntimeError):
    """A database does not have the layout that this version of hotmemory declares."""


class HotdataStore:
    """A `Store` over one Hotdata managed database.

    Create one with `provision` or `open`. `embedder` turns texts into vectors of
    `dimensions` floats. `model` names the embedding model. Both are recorded in the
    database when it is provisioned, and every later open must name the same values.
    `clock` gives `created_at` and the time against which `forget_after` is compared.
    """

    def __init__(
        self,
        client: HotdataClient,
        database: ManagedDatabase,
        *,
        embedder: Embedder,
        model: str,
        dimensions: int,
        clock: Clock = utc_now,
    ) -> None:
        self.client = client
        self.database = database
        self._embedder = embedder
        self._model = model
        self._dimensions = dimensions
        self._clock = clock
        self._lock = threading.Lock()
        self._sleep: Callable[[float], None] = time.sleep

    @classmethod
    def provision(
        cls,
        name: str,
        *,
        embedder: Embedder,
        model: str,
        dimensions: int,
        client: HotdataClient | None = None,
        clock: Clock = utc_now,
    ) -> HotdataStore:
        """Open the database named `name`, or create it if no database has that name.

        Safe to run again. If exactly one database has the name, this opens it after a
        check of its tables, columns, indexes, and recorded embedding model and size.
        If none has the name, this creates the database, declares the four tables, and
        builds the indexes. Raises LayoutError, and creates nothing, if more than one
        database has the name or the one found has another layout. Two processes that
        provision the same new name at the same moment can both create a database.

        `client` defaults to `HotdataClient.from_env()`.
        """
        _check_settings(model, dimensions)
        client = HotdataClient.from_env() if client is None else client
        found = [db for db in client.list_managed_databases() if db.description == name]
        if len(found) > 1:
            ids = ", ".join(sorted(db.id for db in found))
            raise LayoutError(f"{len(found)} databases are named {name!r}: {ids}")
        if found:
            database = found[0]
            _check_layout(client, database, model, dimensions)
        else:
            database = _create(client, name, model, dimensions)
        return cls(
            client, database, embedder=embedder, model=model, dimensions=dimensions, clock=clock
        )

    @classmethod
    def open(
        cls,
        database_id: str,
        *,
        embedder: Embedder,
        model: str,
        dimensions: int,
        client: HotdataClient | None = None,
        clock: Clock = utc_now,
    ) -> HotdataStore:
        """Open the database with the id `database_id`, after the checks of `provision`.

        Raises KeyError if no database has that id, and LayoutError if its layout differs.
        """
        _check_settings(model, dimensions)
        client = HotdataClient.from_env() if client is None else client
        database = client.resolve_managed_database(database_id)
        if database.id != database_id:
            raise KeyError(f"no database has the id {database_id!r}")
        _check_layout(client, database, model, dimensions)
        return cls(
            client, database, embedder=embedder, model=model, dimensions=dimensions, clock=clock
        )

    def put(
        self,
        namespace: Sequence[str],
        key: str,
        *,
        kind: Kind,
        content: str,
        subject: str = "",
        cues: Sequence[str] = (),
        payload: dict[str, JSONValue] | None = None,
        tags: Sequence[str] = (),
        sources: Sequence[str] = (),
        actor: str = "",
        observed_at: datetime | None = None,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        forget_after: datetime | None = None,
        forget_reason: str = "",
    ) -> str:
        draft = build_record(
            namespace,
            key,
            1,
            self._clock(),
            kind=kind,
            content=content,
            subject=subject,
            cues=cues,
            payload=payload,
            tags=tags,
            sources=sources,
            actor=actor,
            observed_at=observed_at,
            valid_from=valid_from,
            valid_until=valid_until,
            forget_after=forget_after,
            forget_reason=forget_reason,
        )
        return self._write_many([draft])[0]

    def delete(self, namespace: Sequence[str], key: str) -> None:
        labels = check_namespace(namespace)
        check_key(key)
        with self._lock:
            where = _slot_condition(labels, key)
            loads: builtins.list[_Load] = []
            for table in RECORD_TABLES:
                rows = self._sql(f"SELECT id FROM {_ref(table)} WHERE {where}").rows
                if rows:
                    ids = pa.table({"id": [row[0] for row in rows]})
                    loads.extend([(table, ids, "delete"), (CUE_TABLE, ids, "delete")])
            self._load_all(loads)

    def writer(
        self, max_rows: int = 1000, interval: timedelta = timedelta(seconds=5)
    ) -> HotdataWriter:
        return HotdataWriter(self, max_rows, interval)

    def _write_many(self, drafts: Sequence[Record]) -> builtins.list[str]:
        """Write `drafts` in order, with one load per table, and return the id of each.

        Each draft follows the rules of `put`. The read of the current revisions and the
        loads hold the lock of this store.
        """
        with self._lock:
            now = self._clock()
            slots = {(draft.namespace, draft.key) for draft in drafts}
            current, vectors = self._current(slots)
            pending: dict[str, Record] = {}
            ids = []
            for draft in drafts:
                slot = (draft.namespace, draft.key)
                found = current.get(slot)
                check_episode_line(found, draft.kind)
                if found is not None and is_duplicate(found, draft.content, now):
                    ids.append(found.id)
                    continue
                record = replace(draft, revision=next_revision(found), created_at=now)
                if found is not None:
                    pending[found.id] = replace(found, superseded_by=record.id)
                pending[record.id] = record
                current[slot] = record
                ids.append(record.id)
            if pending:
                self._load_records(builtins.list(pending.values()), vectors)
            return ids

    def _current(
        self, slots: set[tuple[tuple[str, ...], str]]
    ) -> tuple[dict[tuple[tuple[str, ...], str], Record], dict[str, builtins.list[float]]]:
        """Return the current revision of each slot that has one, and its content vector."""
        current: dict[tuple[tuple[str, ...], str], Record] = {}
        vectors: dict[str, builtins.list[float]] = {}
        if not slots:
            return current, vectors
        where = " OR ".join(f"({_slot_condition(*slot)})" for slot in sorted(slots))
        columns = ", ".join(RECORD_SCHEMA.names)
        for table in RECORD_TABLES:
            sql = f"SELECT {columns} FROM {_ref(table)} WHERE superseded_by IS NULL AND ({where})"
            for row in self._sql(sql).to_records():
                record = _record(row)
                slot = (record.namespace, record.key)
                if slot not in current or current[slot].revision < record.revision:
                    current[slot] = record
                    vectors[record.id] = row["content_embedding"]
        return current, vectors

    def _load_records(
        self, records: Sequence[Record], vectors: dict[str, builtins.list[float]]
    ) -> None:
        """Upsert `records` into their tables, and the cue rows of the new ones."""
        fresh = [record for record in records if record.id not in vectors]
        with_cues = [record for record in fresh if record.cues]
        embedded = self._embed(
            [record.content for record in fresh] + ["\n".join(record.cues) for record in with_cues]
        )
        content = dict(zip((record.id for record in fresh), embedded[: len(fresh)], strict=True))
        cues = dict(zip((record.id for record in with_cues), embedded[len(fresh) :], strict=True))
        by_table: dict[str, builtins.list[dict[str, Any]]] = {}
        for record in records:
            vector = vectors[record.id] if record.id in vectors else content[record.id]
            by_table.setdefault(_table(record.kind), []).append(_row(record, vector))
        loads: builtins.list[_Load] = [
            (table, pa.Table.from_pylist(rows, schema=RECORD_SCHEMA), "upsert")
            for table, rows in by_table.items()
        ]
        if cues:
            cue_rows = {"id": builtins.list(cues), "cues_embedding": builtins.list(cues.values())}
            loads.append((CUE_TABLE, pa.table(cue_rows, schema=CUE_SCHEMA), "upsert"))
        self._load_all(loads)

    def _embed(self, texts: Sequence[str]) -> builtins.list[builtins.list[float]]:
        """Return one vector for each text, or raise ValueError if a vector has another size."""
        if not texts:
            return []
        vectors = [[float(x) for x in vector] for vector in self._embedder(texts)]
        if len(vectors) != len(texts):
            raise ValueError(f"embedder returned {len(vectors)} vectors for {len(texts)} texts")
        for vector in vectors:
            if len(vector) != self._dimensions:
                raise ValueError(
                    f"embedder returned a vector of {len(vector)} floats; "
                    f"this database holds vectors of {self._dimensions}"
                )
        return vectors

    def _load_all(self, loads: Sequence[_Load]) -> None:
        """Run each load, at the same time when there is more than one."""
        if len(loads) == 1:
            self._load(*loads[0])
            return
        if not loads:
            return
        with ThreadPoolExecutor(len(loads)) as pool:
            futures = [pool.submit(self._load, *load) for load in loads]
        for future in futures:
            future.result()

    def _load(self, table: str, rows: pa.Table, mode: ManagedLoadMode) -> None:
        """Upload `rows` once and load them, with a retry when the table is locked.

        A load refused with 409 RESOURCE_LOCKED is tried up to LOAD_ATTEMPTS times, with
        a backoff from FIRST_BACKOFF seconds that doubles up to MAX_BACKOFF seconds.
        """
        with tempfile.TemporaryDirectory() as tmp:
            upload_id = self.client.upload_parquet(_Files(Path(tmp)).write(rows))
        delay = FIRST_BACKOFF
        for attempt in range(1, LOAD_ATTEMPTS + 1):
            try:
                self.client.load_managed_table(
                    self.database,
                    table,
                    schema=SCHEMA,
                    upload_id=upload_id,
                    mode=mode,
                    key=["id"],
                )
                return
            except RuntimeError as error:
                if attempt == LOAD_ATTEMPTS or not _is_locked(error):
                    raise
            self._sleep(delay)
            delay = min(delay * 2, MAX_BACKOFF)

    def _sql(self, sql: str) -> Any:
        return self.client.execute_sql(sql, database=self.database)


class HotdataWriter(BufferedWriter):
    """The `Writer` that `HotdataStore.writer` returns. Each flush sends one load per table."""

    def __init__(self, store: HotdataStore, max_rows: int, interval: timedelta) -> None:
        super().__init__(store._write_many, store._clock, max_rows, interval)


def _check_settings(model: str, dimensions: int) -> None:
    if not isinstance(model, str) or not model:
        raise ValueError("model must be a non-empty string")
    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        raise TypeError("dimensions must be an integer")
    if dimensions < 1:
        raise ValueError("dimensions must be 1 or more")


def _create(client: HotdataClient, name: str, model: str, dimensions: int) -> ManagedDatabase:
    """Create the database, load a seed row into each table, index it, and remove the seeds.

    The meta row is loaded last, so a database without it did not finish provisioning.
    """
    database = client.create_managed_database(
        name, schema=SCHEMA, tables=list(TABLES), keys={table: ["id"] for table in TABLES}
    )
    with tempfile.TemporaryDirectory() as tmp:
        files = _Files(Path(tmp))
        for table in (*RECORD_TABLES, CUE_TABLE):
            seed = files.write(_seed_rows(table, dimensions))
            client.load_managed_table(database, table, schema=SCHEMA, file=seed, mode="replace")
        for index in INDEXES:
            client.create_index(
                database,
                index.table,
                schema=SCHEMA,
                columns=[index.column],
                index_type=index.index_type,
                metric=index.metric,
                poll_interval_s=0.2,
            )
        unseed = files.write(pa.table({"id": [SEED_ID]}))
        for table in (*RECORD_TABLES, CUE_TABLE):
            client.load_managed_table(
                database, table, schema=SCHEMA, file=unseed, mode="delete", key=["id"]
            )
        meta = pa.table(
            {
                "id": [META_ID],
                "schema_version": [SCHEMA_VERSION],
                "model": [model],
                "dimensions": [dimensions],
            },
            schema=META_SCHEMA,
        )
        client.load_managed_table(
            database, META_TABLE, schema=SCHEMA, file=files.write(meta), mode="replace"
        )
    return database


def _seed_rows(table: str, dimensions: int) -> pa.Table:
    """Return one row for `table` that every index can build over."""
    vector = [1.0] * dimensions
    if table == CUE_TABLE:
        return pa.table({"id": [SEED_ID], "cues_embedding": [vector]}, schema=CUE_SCHEMA)
    row: dict[str, Any] = {field.name: None for field in RECORD_SCHEMA}
    row.update(id=SEED_ID, content=SEED_ID, content_embedding=vector)
    return pa.Table.from_pylist([row], schema=RECORD_SCHEMA)


def _check_layout(
    client: HotdataClient, database: ManagedDatabase, model: str, dimensions: int
) -> None:
    """Raise LayoutError unless `database` has the tables, columns, indexes, and meta row."""
    columns = {
        info.table: [column.name for column in info.columns or []]
        for info in client.iter_tables(
            connection_id=database.default_connection_id, include_columns=True
        )
        if info.var_schema == SCHEMA
    }
    missing = [table for table in SCHEMAS if table not in columns]
    if missing:
        raise LayoutError(f"database {database.id} has no table {', '.join(missing)}")
    for table, schema in SCHEMAS.items():
        if sorted(columns[table]) != sorted(schema.names):
            raise LayoutError(
                f"table {table} of database {database.id} has the columns "
                f"{sorted(columns[table])}, expected {sorted(schema.names)}"
            )
    indexes = IndexesApi(client.api)
    built = {
        (table, index.index_type, tuple(index.columns), index.metric)
        for table in (*RECORD_TABLES, CUE_TABLE)
        for index in indexes.list_indexes(database.default_connection_id, SCHEMA, table).indexes
    }
    for wanted in INDEXES:
        if (wanted.table, wanted.index_type, (wanted.column,), wanted.metric) not in built:
            raise LayoutError(
                f"table {wanted.table} of database {database.id} has no "
                f"{wanted.index_type} index on {wanted.column}"
            )
    rows = client.execute_sql(
        f"SELECT schema_version, model, dimensions FROM {_ref(META_TABLE)} WHERE id = '{META_ID}'",
        database=database,
    ).rows
    if len(rows) != 1:
        raise LayoutError(f"database {database.id} did not finish provisioning: no meta row")
    found = (int(rows[0][0]), str(rows[0][1]), int(rows[0][2]))
    if found != (SCHEMA_VERSION, model, dimensions):
        raise LayoutError(
            f"database {database.id} records schema version {found[0]}, model {found[1]!r}, "
            f"and {found[2]} dimensions; this store has schema version {SCHEMA_VERSION}, "
            f"model {model!r}, and {dimensions} dimensions"
        )


def _table(kind: Kind) -> str:
    """Return the table that holds records of `kind`."""
    return EPISODE_TABLE if kind == "episode" else MEMORY_TABLE


def _text(value: str) -> str:
    """Return `value` as a SQL string literal."""
    return "'" + value.replace("'", "''") + "'"


def _slot_condition(namespace: tuple[str, ...], key: str) -> str:
    """Return the SQL condition that matches the rows of one key."""
    return f"namespace = {_text('/'.join(namespace))} AND key = {_text(key)}"


def _row(record: Record, vector: Sequence[float]) -> dict[str, Any]:
    """Return the table row of `record`, with `vector` as its content embedding."""
    return {
        "id": record.id,
        "namespace": "/".join(record.namespace),
        "key": record.key,
        "revision": record.revision,
        "kind": record.kind,
        "subject": record.subject,
        "content": record.content,
        "cues": list(record.cues),
        "payload": json.dumps(record.payload, sort_keys=True),
        "tags": list(record.tags),
        "sources": list(record.sources),
        "actor": record.actor,
        "created_at": record.created_at,
        "observed_at": record.observed_at,
        "valid_from": record.valid_from,
        "valid_until": record.valid_until,
        "expired_at": record.expired_at,
        "superseded_by": record.superseded_by,
        "forget_after": record.forget_after,
        "forget_reason": record.forget_reason,
        "content_embedding": list(vector),
    }


def _record(row: dict[str, Any]) -> Record:
    """Return the record that a table row holds."""
    return Record(
        namespace=tuple(row["namespace"].split("/")),
        key=row["key"],
        revision=int(row["revision"]),
        kind=row["kind"],
        subject=row["subject"],
        content=row["content"],
        cues=tuple(row["cues"] or ()),
        payload=json.loads(row["payload"]),
        tags=tuple(row["tags"] or ()),
        sources=tuple(row["sources"] or ()),
        actor=row["actor"],
        created_at=_time(row["created_at"]),
        observed_at=_time(row["observed_at"]),
        valid_from=_time(row["valid_from"]),
        valid_until=_time(row["valid_until"]),
        expired_at=_time(row["expired_at"]),
        superseded_by=row["superseded_by"],
        forget_after=_time(row["forget_after"]),
        forget_reason=row["forget_reason"],
    )


def _time(value: Any) -> Any:
    """Return a timestamp that a query returned as a datetime, or None."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _is_locked(error: RuntimeError) -> bool:
    """Return True if `error` is a refusal because another load holds the table."""
    cause = error.__cause__
    if getattr(error, "code", None) == "RESOURCE_LOCKED":
        return True
    return getattr(cause, "status", None) == 409 and "RESOURCE_LOCKED" in str(
        getattr(cause, "body", "")
    )


def _ref(table: str) -> str:
    """Return the SQL name of `table` inside a managed database."""
    return f"default.{SCHEMA}.{table}"


class _Files:
    """Writes parquet files with distinct names into one directory."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._count = 0

    def write(self, table: pa.Table) -> str:
        self._count += 1
        path = self._directory / f"{self._count}.parquet"
        pq.write_table(table, path)
        return str(path)
