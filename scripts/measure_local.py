# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "hotdata-framework>=0.14.1",
#   "pyarrow>=14.0",
# ]
# ///
"""Run measurements M6, M4, and M5 against a local RuntimeDB container or the cloud.

By default the script targets a running local container. It does not start the
container. Start it with `make local-up`, which sets RUNTIMEDB_ENGINE__SQL_WRITES=true
for M4. With --cloud, it targets the Hotdata cloud API instead.

Reads the connection from the environment:

- HOTMEMORY_LOCAL_URL: optional, local only. The container URL. Defaults to
  http://localhost:3000. Locally the script ignores HOTDATA_API_URL, HOTDATA_API_KEY,
  and HOTDATA_WORKSPACE, and sends the placeholder key and workspace "local", so a
  .env that holds cloud credentials never sends them to the container.
- HOTDATA_API_KEY: required with --cloud.
- HOTDATA_WORKSPACE: optional with --cloud. Without it, the framework picks the
  active workspace.
- HOTDATA_API_URL: optional with --cloud. Defaults to https://api.hotdata.dev.
- HOTMEMORY_MEASURE_DB: required with --cloud. The prefix for the throwaway database
  names; each measurement appends -m4, -m5, or -m6. The script refuses to run when a
  database with one of those names already exists.
- HOTMEMORY_EMBEDDING_PROVIDER: optional. The provider that M6 and M5 try for a
  provider-backed vector index. Defaults to sys_emb_openai.
- HOTMEMORY_M5_SIZES: optional. Comma-separated row counts for M5. Defaults to
  1000,10000,100000.

M6 runs first. Locally, its first two calls are the proof that the container answers
a query with no API key and no control plane. Each measurement creates its own
database and deletes it on exit.

Prints the results as Markdown, ready to paste into docs/guarantees.md.
"""

from __future__ import annotations

import os
import random
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from hotdata_framework import HotdataClient, ManagedDatabase
from hotdata_framework.env import default_api_key, default_host, pick_workspace

SCHEMA = "public"
M4_CALLS = 100
M5_DIMENSIONS = 64
M5_RUNS = 5
M5_DEPTH = 100
M5_K = 10
RRF_K = 60

WORDS = [
    "alert",
    "billing",
    "checkout",
    "disk",
    "pool",
    "certificate",
    "deploy",
    "flag",
    "resolver",
    "memory",
    "pod",
    "queue",
    "shard",
    "replica",
    "latency",
    "timeout",
    "retry",
    "webhook",
    "cache",
    "tenant",
    "session",
    "token",
    "gateway",
    "scheduler",
    "exporter",
    "partition",
    "lock",
    "index",
    "ingest",
    "ledger",
    "auth",
    "notify",
    "search",
    "region",
    "rollback",
    "migration",
    "quota",
    "throttle",
    "backlog",
    "cron",
    "invoice",
    "payload",
    "schema",
]
NAMESPACES = ["team/payments", "team/platform", "team/search", "team/identity"]
KINDS = ["fact", "fact", "fact", "profile", "procedure"]


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        sys.exit(f"{name} must be set")
    return value


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def connect() -> HotdataClient:
    return HotdataClient("local", "local", host=env("HOTMEMORY_LOCAL_URL", "http://localhost:3000"))


def connect_cloud() -> HotdataClient:
    api_key = env("HOTDATA_API_KEY", default_api_key())
    host = default_host()
    return HotdataClient(api_key, pick_workspace(api_key, host), host=host)


def write_parquet(directory: Path, name: str, table: pa.Table) -> str:
    path = directory / f"{name}.parquet"
    pq.write_table(table, path)
    return str(path)


def table_ref(table: str) -> str:
    return f"default.{SCHEMA}.{table}"


def vector_literal(values: list[float]) -> str:
    return "ARRAY[" + ", ".join(repr(float(v)) for v in values) + "]"


def timed(fn: Callable[[], Any]) -> float:
    started = time.perf_counter()
    fn()
    return time.perf_counter() - started


def short(error: BaseException) -> str:
    return " ".join(str(error).split())[:300]


# M6: every framework call the driver needs, against the bare container.


def measure_bare(
    client: HotdataClient, work: Path, provider: str, prefix: str
) -> list[tuple[str, str]]:
    steps: list[tuple[str, str]] = []
    db: ManagedDatabase | None = None

    def step(label: str, fn: Callable[[], Any]) -> Any:
        try:
            value = fn()
        except Exception as e:  # noqa: BLE001
            steps.append((label, f"fails: {short(e)}"))
            return None
        steps.append((label, "works"))
        return value

    db = step(
        "create a managed database with two keyed tables",
        lambda: client.create_managed_database(
            f"{prefix}-m6", tables=["memory", "episode"], keys={"memory": ["id"], "episode": ["id"]}
        ),
    )
    if db is None:
        return steps
    try:
        step("query SELECT 1", lambda: client.execute_sql("SELECT 1", database=db))
        rows = pa.table({"id": ["a", "b", "c"], "content": ["disk full", "pool empty", "cert"]})
        step(
            "load in replace mode",
            lambda: client.load_managed_table(
                db, "memory", file=write_parquet(work, "m6_replace", rows), mode="replace"
            ),
        )
        upsert = pa.table({"id": ["c", "d"], "content": ["certificate expired", "dns timeout"]})
        step(
            "load in upsert mode with key id",
            lambda: client.load_managed_table(
                db,
                "memory",
                file=write_parquet(work, "m6_upsert", upsert),
                mode="upsert",
                key=["id"],
            ),
        )
        step(
            "load in delete mode with key id",
            lambda: client.load_managed_table(
                db,
                "memory",
                file=write_parquet(work, "m6_delete", pa.table({"id": ["a"]})),
                mode="delete",
                key=["id"],
            ),
        )
        count = step(
            "query the loaded table",
            lambda: client.execute_sql(f"SELECT count(*) FROM {table_ref('memory')}", database=db),
        )
        if count is not None:
            steps.append(("row count after the loads (expected 3)", str(count.rows[0][0])))
        step(
            "build a BM25 index",
            lambda: client.create_index(db, "memory", columns=["content"], index_type="bm25"),
        )
        step(
            "query bm25_search",
            lambda: client.execute_sql(
                f"SELECT id, score FROM bm25_search('{table_ref('memory')}', 'content', "
                f"'certificate', 5) ORDER BY score DESC",
                database=db,
            ),
        )
        step(
            "load the episode table",
            lambda: client.load_managed_table(
                db, "episode", file=write_parquet(work, "m6_episode", rows), mode="replace"
            ),
        )
        step(
            f"build a provider-backed vector index ({provider})",
            lambda: client.create_index(
                db,
                "episode",
                columns=["content"],
                index_type="vector",
                embedding_provider_id=provider,
                timeout_s=120,
            ),
        )
        step(
            "query vector_search",
            lambda: client.execute_sql(
                f"SELECT id, _distance FROM vector_search('{table_ref('episode')}', 'content', "
                f"'certificate', 5) ORDER BY _distance ASC",
                database=db,
            ),
        )
    finally:
        step("delete the managed database", lambda: client.delete_managed_database(db))
    return steps


# M4: SQL INSERT against upload-and-load, one row per call.


def measure_insert(client: HotdataClient, work: Path, prefix: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    db = client.create_managed_database(f"{prefix}-m4", tables=["by_load", "by_sql"])
    try:
        seed = pa.table({"id": ["seed"], "content": ["seed"]})
        for table in ("by_load", "by_sql"):
            client.load_managed_table(
                db, table, file=write_parquet(work, f"m4_{table}", seed), mode="replace"
            )

        sql_times: list[float] = []
        for n in range(M4_CALLS):
            sql = f"INSERT INTO {table_ref('by_sql')} VALUES ('sql-{n}', 'row {n}')"
            try:
                sql_times.append(timed(lambda sql=sql: client.execute_sql(sql, database=db)))
            except RuntimeError as e:
                out["sql_error"] = short(e)
                break
        out["sql"] = sql_times

        paths = [
            write_parquet(
                work, f"m4_load_{n}", pa.table({"id": [f"load-{n}"], "content": [f"row {n}"]})
            )
            for n in range(M4_CALLS)
        ]
        load_times: list[float] = []
        for path in paths:
            load_times.append(
                timed(
                    lambda path=path: client.load_managed_table(
                        db, "by_load", file=path, mode="append"
                    )
                )
            )
        out["load"] = load_times

        for table in ("by_load", "by_sql"):
            result = client.execute_sql(f"SELECT count(*) FROM {table_ref(table)}", database=db)
            out[f"{table}_rows"] = int(result.rows[0][0])
    finally:
        client.delete_managed_database(db)
    return out


# M5: the three-stage retrieval query at three sizes, with and without indexes.


def memory_rows(size: int, rng: random.Random) -> pa.Table:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    created = [base + timedelta(minutes=i) for i in range(size)]
    vectors = [[rng.uniform(-1.0, 1.0) for _ in range(M5_DIMENSIONS)] for _ in range(size)]
    return pa.table(
        {
            "id": [f"r{i}" for i in range(size)],
            "namespace": [NAMESPACES[i % len(NAMESPACES)] for i in range(size)],
            "key": [f"k{i}" for i in range(size)],
            "revision": pa.array([1] * size, pa.int64()),
            "kind": [KINDS[i % len(KINDS)] for i in range(size)],
            "subject": [f"service-{i % 50}" for i in range(size)],
            "content": [" ".join(rng.choices(WORDS, k=12)) for _ in range(size)],
            "tags": [[WORDS[i % len(WORDS)]] for i in range(size)],
            "actor": ["loader"] * size,
            "created_at": pa.array(created, pa.timestamp("us", tz="UTC")),
            "valid_from": pa.array(created, pa.timestamp("us", tz="UTC")),
            "valid_until": pa.array([None] * size, pa.timestamp("us", tz="UTC")),
            "superseded_by": pa.array([None] * size, pa.string()),
            "forget_after": pa.array([None] * size, pa.timestamp("us", tz="UTC")),
            "embedding": pa.array(vectors, pa.list_(pa.float32())),
        }
    )


FILTERS = (
    "(namespace = 'team/payments' OR namespace LIKE 'team/payments/%') "
    "AND kind = 'fact' AND superseded_by IS NULL "
    "AND (valid_from IS NULL OR valid_from <= TIMESTAMP '2027-01-01 00:00:00') "
    "AND (valid_until IS NULL OR valid_until > TIMESTAMP '2027-01-01 00:00:00') "
    "AND (forget_after IS NULL OR forget_after > TIMESTAMP '2027-01-01 00:00:00')"
)


def queries(table: str, word: str, vector: list[float]) -> dict[str, str]:
    ref = table_ref(table)
    lit = vector_literal(vector)
    fused = (
        f"WITH near AS (SELECT id, cosine_distance(embedding, {lit}) AS d FROM {ref} "
        f"ORDER BY d ASC LIMIT {M5_DEPTH}), "
        f"near_ranked AS (SELECT id, ROW_NUMBER() OVER (ORDER BY d ASC) AS r FROM near), "
        f"text_ranked AS (SELECT id, ROW_NUMBER() OVER (ORDER BY score DESC) AS r "
        f"FROM bm25_search('{ref}', 'content', '{word}', {M5_DEPTH})), "
        f"fused AS (SELECT COALESCE(n.id, t.id) AS id, "
        f"COALESCE(1.0 / ({RRF_K} + n.r), 0) + COALESCE(1.0 / ({RRF_K} + t.r), 0) AS s "
        f"FROM near_ranked n FULL OUTER JOIN text_ranked t ON n.id = t.id) "
        f"SELECT b.id, f.s FROM fused f JOIN {ref} b ON b.id = f.id "
        f"WHERE {FILTERS} ORDER BY f.s DESC, b.id ASC LIMIT {M5_K}"
    )
    return {
        "filter scan": (
            f"SELECT id FROM {ref} WHERE {FILTERS} ORDER BY created_at DESC LIMIT {M5_K}"
        ),
        "filtered vector rank": (
            f"SELECT id, cosine_distance(embedding, {lit}) AS d FROM {ref} "
            f"WHERE {FILTERS} ORDER BY d ASC LIMIT {M5_K}"
        ),
        "unfiltered vector rank": (
            f"SELECT id, cosine_distance(embedding, {lit}) AS d FROM {ref} "
            f"ORDER BY d ASC LIMIT {M5_K}"
        ),
        "text match scan": (
            f"SELECT id FROM {ref} WHERE {FILTERS} AND content LIKE '%{word}%' "
            f"ORDER BY created_at DESC LIMIT {M5_K}"
        ),
        "bm25_search": (
            f"SELECT id, score FROM bm25_search('{ref}', 'content', '{word}', {M5_K}) "
            f"ORDER BY score DESC"
        ),
        "fused three-stage": fused,
    }


def median_time(client: HotdataClient, db: ManagedDatabase, sql: str) -> str:
    try:
        client.execute_sql(sql, database=db)
        runs = [timed(lambda: client.execute_sql(sql, database=db)) for _ in range(M5_RUNS)]
    except RuntimeError as e:
        return f"fails: {short(e)}"
    return f"{statistics.median(runs) * 1000:.0f} ms"


def measure_scale(
    client: HotdataClient, work: Path, sizes: list[int], provider: str, prefix: str
) -> dict:
    rng = random.Random(7)
    tables = [f"m5_{size}" for size in sizes]
    out: dict[str, Any] = {"sizes": {}, "coexistence": []}
    db = client.create_managed_database(f"{prefix}-m5", tables=tables)
    try:
        for size, table in zip(sizes, tables, strict=True):
            entry: dict[str, Any] = {}
            data = memory_rows(size, rng)
            path = write_parquet(work, table, data)
            entry["load_s"] = timed(
                lambda path=path, table=table: client.load_managed_table(
                    db, table, file=path, mode="replace"
                )
            )
            vector = [rng.uniform(-1.0, 1.0) for _ in range(M5_DIMENSIONS)]
            sqls = queries(table, "certificate", vector)
            entry["before"] = {name: median_time(client, db, sql) for name, sql in sqls.items()}

            builds: dict[str, str] = {}
            for label, kwargs in (
                ("bm25 on content", {"columns": ["content"], "index_type": "bm25"}),
                (
                    "plain vector on embedding (cosine)",
                    {"columns": ["embedding"], "index_type": "vector", "metric": "cosine"},
                ),
                ("sorted on created_at", {"columns": ["created_at"], "index_type": "sorted"}),
            ):
                try:
                    seconds = timed(
                        lambda kwargs=kwargs, table=table: client.create_index(
                            db, table, timeout_s=1800, **kwargs
                        )
                    )
                    builds[label] = f"{seconds:.1f} s"
                except Exception as e:  # noqa: BLE001
                    builds[label] = f"fails: {short(e)}"
            entry["builds"] = builds
            entry["after"] = {name: median_time(client, db, sql) for name, sql in sqls.items()}
            out["sizes"][size] = entry

        first = tables[0]
        try:
            client.create_index(
                db,
                first,
                columns=["subject"],
                index_type="vector",
                embedding_provider_id=provider,
                timeout_s=120,
            )
            out["coexistence"].append(
                ("provider-backed vector beside BM25 and plain vector", "accepted")
            )
        except Exception as e:  # noqa: BLE001
            out["coexistence"].append(
                ("provider-backed vector beside BM25 and plain vector", f"refused: {short(e)}")
            )
    finally:
        client.delete_managed_database(db)
    return out


def summary(times: list[float]) -> str:
    if not times:
        return "no calls completed"
    ordered = sorted(times)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return (
        f"{len(times)} calls, median {statistics.median(times) * 1000:.0f} ms, "
        f"p95 {p95 * 1000:.0f} ms, mean {statistics.mean(times) * 1000:.0f} ms"
    )


def main() -> int:
    provider = os.environ.get("HOTMEMORY_EMBEDDING_PROVIDER", "sys_emb_openai")
    sizes = [int(s) for s in os.environ.get("HOTMEMORY_M5_SIZES", "1000,10000,100000").split(",")]
    cloud = "--cloud" in sys.argv[1:]
    if cloud:
        prefix = env("HOTMEMORY_MEASURE_DB")
        client = connect_cloud()
        names = {f"{prefix}-{m}" for m in ("m4", "m5", "m6")}
        taken = sorted(names & {d.description for d in client.list_managed_databases()})
        if taken:
            sys.exit(
                f"databases already exist: {', '.join(taken)}; pick a new HOTMEMORY_MEASURE_DB"
            )
    else:
        prefix = "hotmemory"
        client = connect()
    started_at = now()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        bare = measure_bare(client, work, provider, prefix)
        print("m6 done", file=sys.stderr)
        try:
            insert = measure_insert(client, work, prefix)
        except RuntimeError as e:
            insert = {"setup_error": short(e)}
        print("m4 done", file=sys.stderr)
        try:
            scale = measure_scale(client, work, sizes, provider, prefix)
        except RuntimeError as e:
            scale = {"sizes": {}, "coexistence": [], "setup_error": short(e)}
        print("m5 done", file=sys.stderr)

    print(f"## {'Cloud' if cloud else 'Local'} measurements of M4 to M6, {started_at}")
    print()
    print(f"Host: {client.host}.")
    print()
    print("### M6")
    print()
    for label, outcome in bare:
        print(f"- {label}: {outcome}")
    print()
    print("### M4")
    print()
    if "setup_error" in insert:
        print(f"- setup fails: {insert['setup_error']}")
    print(f"- SQL INSERT, one row per statement: {summary(insert.get('sql', []))}.")
    if "sql_error" in insert:
        print(f"  - error: {insert['sql_error']}")
    print(f"- Upload and append load, one row per call: {summary(insert.get('load', []))}.")
    print(
        f"- Rows after the calls: by_sql {insert.get('by_sql_rows')}, "
        f"by_load {insert.get('by_load_rows')} (each includes one seed row)."
    )
    print()
    print("### M5")
    print()
    if "setup_error" in scale:
        print(f"- setup fails: {scale['setup_error']}")
    print(
        f"Median of {M5_RUNS} runs after one warm-up, {M5_DIMENSIONS}-dimension embeddings, "
        f"k {M5_K}, fusion depth {M5_DEPTH}."
    )
    for size, entry in scale["sizes"].items():
        print()
        print(f"#### {size} rows (load {entry['load_s']:.1f} s)")
        print()
        for label, outcome in entry["builds"].items():
            print(f"- index build, {label}: {outcome}")
        print()
        print("| Query | Without indexes | With indexes |")
        print("|---|---|---|")
        for name in entry["before"]:
            print(f"| {name} | {entry['before'][name]} | {entry['after'][name]} |")
    print()
    for label, outcome in scale["coexistence"]:
        print(f"- {label}: {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
