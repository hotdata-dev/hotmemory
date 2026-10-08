"""`HotdataStore`: the driver that keeps records in one Hotdata managed database.

Needs the `hotdata` extra: `pip install hotmemory[hotdata]`.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from hotdata.api.indexes_api import IndexesApi
from hotdata_framework import HotdataClient, ManagedDatabase
from hotdata_framework.client import IndexType, VectorMetric

from hotmemory.memory import utc_now
from hotmemory.record import SCHEMA_VERSION
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
