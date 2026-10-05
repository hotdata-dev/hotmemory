# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "hotdata-framework>=0.14.1",
#   "pyarrow>=14.0",
# ]
# ///
"""Run measurements M1, M2, and M3 against a throwaway Hotdata cloud database.

Reads the connection from the environment:

- HOTDATA_API_KEY: required.
- HOTDATA_WORKSPACE: optional. Without it, the framework picks the active workspace.
- HOTDATA_API_URL: optional. Defaults to https://api.hotdata.dev.
- HOTMEMORY_MEASURE_DB: required. The name of the throwaway database. The script
  refuses to run when a database with this name already exists, creates it, and
  deletes it on exit.
- HOTMEMORY_EMBEDDING_PROVIDER: optional. The provider for the provider-backed
  vector index. Defaults to sys_emb_openai.

M1 and M2 run once against a provider-backed vector index and once against a BM25
index, on separate tables, because a provider-backed index cannot share a table with
another index. M3 runs two processes that load one table at the same time.

Prints the results as Markdown, ready to paste into docs/guarantees.md.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from hotdata_framework import HotdataClient, ManagedDatabase
from hotdata_framework.env import default_api_key, default_host, pick_workspace

SCHEMA = "public"
VECTOR_TABLE = "m1_vector"
BM25_TABLE = "m1_bm25"
LOCK_TABLE = "m3_lock"
BASE_ROWS = 50
NEW_ROWS = 10
SERVE_TIMEOUT_S = 120.0
SERVE_POLL_S = 2.0
FORGET_WAIT_S = 30.0

M3_LOADS_PER_PROCESS = 10
M3_MAX_ATTEMPTS = 8
M3_BACKOFF_BASE_S = 0.25
M3_BACKOFF_CAP_S = 4.0

SERVICES = ["billing", "checkout", "search", "ingest", "auth", "ledger", "notify", "export"]
SYMPTOMS = [
    "disk filled on the primary volume",
    "connection pool ran out of slots",
    "certificate expired at midnight",
    "a deploy shipped a bad feature flag",
    "the upstream DNS resolver timed out",
    "memory grew until the pod was killed",
]
NEW_FACTS = [
    "The aurora gateway rejects tokens signed with the retired zebra key",
    "Saturn queue consumers stall when the walrus partition rebalances",
    "The marigold cron job double-bills invoices on leap days",
    "Penguin cache entries outlive their tenant after a quokka migration",
    "The obsidian exporter drops rows whose payload exceeds the tangerine limit",
    "Heron workers deadlock when the lantern lock is taken twice",
    "The cobalt webhook retries forever after a pelican timeout",
    "Falcon shards lose writes when the meadow replica lags",
    "The juniper scheduler skips jobs during the otter maintenance window",
    "Ivory sessions survive logout when the badger flag is on",
]


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        sys.exit(f"{name} must be set")
    return value


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def base_contents() -> list[str]:
    return [
        f"{SERVICES[i % len(SERVICES)]} paged because the {SYMPTOMS[i % len(SYMPTOMS)]} "
        f"(incident {i})"
        for i in range(BASE_ROWS)
    ]


def write_parquet(directory: Path, name: str, rows: dict[str, list[Any]]) -> str:
    path = directory / f"{name}.parquet"
    pq.write_table(pa.table(rows), path)
    return str(path)


def connect() -> HotdataClient:
    api_key = env("HOTDATA_API_KEY", default_api_key())
    host = default_host()
    return HotdataClient(api_key, pick_workspace(api_key, host), host=host)


def is_locked(error: BaseException) -> bool:
    cause = error.__cause__
    return getattr(cause, "status", None) == 409 or "RESOURCE_LOCKED" in str(error)


def table_ref(table: str) -> str:
    return f"default.{SCHEMA}.{table}"


def search_ids(
    client: HotdataClient, db: ManagedDatabase, kind: str, table: str, text: str
) -> list[str]:
    function = "vector_search" if kind == "vector" else "bm25_search"
    order = "_distance ASC" if kind == "vector" else "score DESC"
    quoted = text.replace("'", "''")
    sql = (
        f"SELECT id FROM {function}('{table_ref(table)}', 'content', '{quoted}', 5) "
        f"ORDER BY {order}"
    )
    result = client.execute_sql(sql, database=db)
    return [str(row[0]) for row in result.rows]


@dataclass
class IndexResult:
    kind: str
    build_s: float = 0.0
    load_s: float = 0.0
    served: bool = False
    served_after_s: float | None = None
    deleted_row_count: int | None = None
    deleted_returned_at_once: bool | None = None
    deleted_returned_after_wait: bool | None = None
    errors: list[str] = field(default_factory=list)


def measure_index(
    client: HotdataClient, db: ManagedDatabase, kind: str, table: str, work: Path, provider: str
) -> IndexResult:
    result = IndexResult(kind=kind)
    contents = base_contents()
    ids = [f"base-{i}" for i in range(BASE_ROWS)]
    client.load_managed_table(
        db,
        table,
        file=write_parquet(work, f"{table}_base", {"id": ids, "content": contents}),
        mode="replace",
    )

    started = time.perf_counter()
    try:
        if kind == "vector":
            client.create_index(
                db,
                table,
                columns=["content"],
                index_type="vector",
                embedding_provider_id=provider,
            )
        else:
            client.create_index(db, table, columns=["content"], index_type="bm25")
    except (RuntimeError, TimeoutError) as e:
        result.errors.append(f"index build: {e}")
        return result
    result.build_s = time.perf_counter() - started

    new_ids = [f"new-{i}" for i in range(NEW_ROWS)]
    started = time.perf_counter()
    client.load_managed_table(
        db,
        table,
        file=write_parquet(work, f"{table}_new", {"id": new_ids, "content": NEW_FACTS}),
        mode="upsert",
        key=["id"],
    )
    result.load_s = time.perf_counter() - started

    # M1: is a row loaded after the build served by the index?
    target_id, target_text = new_ids[3], NEW_FACTS[3]
    loaded_at = time.perf_counter()
    while time.perf_counter() - loaded_at < SERVE_TIMEOUT_S:
        try:
            if target_id in search_ids(client, db, kind, table, target_text):
                result.served = True
                result.served_after_s = time.perf_counter() - loaded_at
                break
        except RuntimeError as e:
            result.errors.append(f"M1 search: {e}")
            break
        time.sleep(SERVE_POLL_S)

    # M2: does the index still return a row after a keyed delete?
    victim_id, victim_text = new_ids[5], NEW_FACTS[5]
    try:
        client.load_managed_table(
            db,
            table,
            file=write_parquet(work, f"{table}_delete", {"id": [victim_id]}),
            mode="delete",
            key=["id"],
        )
    except RuntimeError as e:
        result.errors.append(f"M2 delete load: {e}")
        return result
    count = client.execute_sql(
        f"SELECT count(*) FROM {table_ref(table)} WHERE id = '{victim_id}'", database=db
    )
    result.deleted_row_count = int(count.rows[0][0])
    try:
        result.deleted_returned_at_once = victim_id in search_ids(
            client, db, kind, table, victim_text
        )
        time.sleep(FORGET_WAIT_S)
        result.deleted_returned_after_wait = victim_id in search_ids(
            client, db, kind, table, victim_text
        )
    except RuntimeError as e:
        result.errors.append(f"M2 search: {e}")
    return result


@dataclass
class WriterResult:
    process: int
    loads_ok: int = 0
    loads_given_up: int = 0
    locked_responses: int = 0
    max_attempts_used: int = 0
    elapsed_s: float = 0.0
    errors: list[str] = field(default_factory=list)


def serialized_load(
    client: HotdataClient, db: ManagedDatabase, path: str, stats: WriterResult
) -> None:
    for attempt in range(1, M3_MAX_ATTEMPTS + 1):
        try:
            client.load_managed_table(db, LOCK_TABLE, file=path, mode="upsert", key=["id"])
            stats.loads_ok += 1
            stats.max_attempts_used = max(stats.max_attempts_used, attempt)
            return
        except RuntimeError as e:
            if not is_locked(e):
                stats.errors.append(str(e))
                return
            stats.locked_responses += 1
            time.sleep(min(M3_BACKOFF_CAP_S, M3_BACKOFF_BASE_S * 2 ** (attempt - 1)))
    stats.loads_given_up += 1
    stats.max_attempts_used = M3_MAX_ATTEMPTS


def writer_process(process: int, db_id: str, barrier: Any, results: Any) -> None:
    client = connect()
    db = client.resolve_managed_database(db_id)
    stats = WriterResult(process=process)
    with tempfile.TemporaryDirectory() as tmp:
        paths = [
            write_parquet(
                Path(tmp),
                f"p{process}_{n}",
                {"id": [f"p{process}-{n}"], "content": [f"writer {process} load {n}"]},
            )
            for n in range(M3_LOADS_PER_PROCESS)
        ]
        barrier.wait()
        started = time.perf_counter()
        for path in paths:
            serialized_load(client, db, path, stats)
        stats.elapsed_s = time.perf_counter() - started
    results.put(stats)


def measure_lock(client: HotdataClient, db: ManagedDatabase, work: Path) -> list[WriterResult]:
    client.load_managed_table(
        db,
        LOCK_TABLE,
        file=write_parquet(work, "lock_seed", {"id": ["seed"], "content": ["seed"]}),
        mode="replace",
    )
    context = mp.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(target=writer_process, args=(n, db.id, barrier, results)) for n in (1, 2)
    ]
    for process in processes:
        process.start()
    collected = [results.get(timeout=600) for _ in processes]
    for process in processes:
        process.join()
    return sorted(collected, key=lambda r: r.process)


def report_index(label: str, r: IndexResult) -> None:
    print(
        f"- {label} index ({r.kind}): build {r.build_s:.1f} s, "
        f"load of {NEW_ROWS} rows {r.load_s:.1f} s."
    )
    if r.served:
        print(f"  - M1: the new row was served {r.served_after_s:.1f} s after the load returned.")
    else:
        print(f"  - M1: the new row was not served within {SERVE_TIMEOUT_S:.0f} s.")
    print(
        f"  - M2: after the delete, a scan counts {r.deleted_row_count} row(s) for the id. "
        f"The index returned it at once: {r.deleted_returned_at_once}. "
        f"After {FORGET_WAIT_S:.0f} s: {r.deleted_returned_after_wait}."
    )
    for error in r.errors:
        print(f"  - error: {error}")


def main() -> int:
    name = env("HOTMEMORY_MEASURE_DB")
    provider = os.environ.get("HOTMEMORY_EMBEDDING_PROVIDER", "sys_emb_openai")
    client = connect()
    if any(d.description == name for d in client.list_managed_databases()):
        sys.exit(f"a database named {name!r} already exists; pick a new HOTMEMORY_MEASURE_DB")

    started_at = now()
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    tables = [VECTOR_TABLE, BM25_TABLE, LOCK_TABLE]
    db = client.create_managed_database(
        name, tables=tables, keys={t: ["id"] for t in tables}, expires_at=expires
    )
    print(f"created database {name} ({db.id}) on {client.host}", file=sys.stderr)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            vector = measure_index(client, db, "vector", VECTOR_TABLE, work, provider)
            bm25 = measure_index(client, db, "bm25", BM25_TABLE, work, provider)
            writers = measure_lock(client, db, work)
    finally:
        client.delete_managed_database(db)
        print(f"deleted database {name} ({db.id})", file=sys.stderr)

    print(f"## Cloud measurements, {started_at}")
    print()
    print(f"Host: {client.host}. Embedding provider: {provider}.")
    print()
    report_index("Provider-backed vector", vector)
    report_index("BM25", bm25)
    print(
        f"- M3: two processes, {M3_LOADS_PER_PROCESS} single-row upsert loads each, "
        f"retry bound {M3_MAX_ATTEMPTS} attempts with backoff {M3_BACKOFF_BASE_S} s doubling "
        f"to {M3_BACKOFF_CAP_S} s."
    )
    for w in writers:
        print(
            f"  - process {w.process}: {w.loads_ok} loaded, {w.loads_given_up} gave up, "
            f"{w.locked_responses} responses were 409, most attempts for one load "
            f"{w.max_attempts_used}, {w.elapsed_s:.1f} s in total."
        )
        for error in w.errors:
            print(f"    - error: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
