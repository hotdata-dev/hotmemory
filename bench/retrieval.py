"""The retrieval score: one episode for each session, a search, and the metrics of each question.

Run it from the root of the repository:

    uv run --group bench --env-file .env python -m bench.retrieval

It writes one JSON file into `bench/results/` with the configuration and the results.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal

from bench import embed
from bench.data import DATA_DIR, DATA_FILE, DATA_REVISION, DATA_SHA256, Question, load
from bench.episodes import scope, session_of, write_episodes
from bench.metrics import KS, mean, score
from hotmemory import Embedder, Filter, Hit, MemoryStore, Store

RESULTS_DIR = Path(__file__).resolve().parent / "results"
K = 50
FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)
RETRIES = 3
DROPPED = "Remote end closed connection without response"
Mode = Literal["isolated", "shared"]
RANKINGS = ("fused", "vector")
ABLATED = ("bm25-content", "content-cue", "bm25")


@dataclass
class Target:
    """The stores of one run: one store for each ranking, all over the same records."""

    driver: str
    location: str
    stores: dict[str, Store]
    empty: Callable[[Store, Sequence[str]], None]
    engine: dict[str, Any]


def search(store: Store, question: Question, namespace: Sequence[str]) -> list[tuple[str, float]]:
    """Return the ranked sessions of `question` and their content distances."""
    hits: list[Hit] = store.search(question.question, [namespace], Filter(kind="episode"), k=K)
    return [(session_of(hit.record.sources), round(hit.distance or 0.0, 6)) for hit in hits]


def run(
    questions: Sequence[Question], target: Target, mode: Mode, run_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Write, search, and score each question, and return the rows and the timings.

    In `isolated` mode, each question is written, searched, and deleted before the next.
    In `shared` mode, every question is written first, and then each one is searched.
    """
    first = next(iter(target.stores.values()))
    rows: list[dict[str, Any]] = []
    write_s: list[float] = []
    delete_s: list[float] = []
    search_ms: dict[str, list[float]] = {name: [] for name in target.stores}

    def write(question: Question) -> None:
        started = time.perf_counter()
        retried(lambda: write_episodes(first, scope(run_id, question), question), target)
        write_s.append(time.perf_counter() - started)

    def query(question: Question) -> None:
        namespace = scope(run_id, question)
        ranked = {}
        for name, store in target.stores.items():
            started = time.perf_counter()
            ranked[name] = search(store, question, namespace)
            search_ms[name].append((time.perf_counter() - started) * 1000)
        rows.append(
            {
                "id": question.id,
                "type": question.type,
                "evidence": sorted(question.evidence),
                "episodes": len(question.episodes()),
                "ranked": ranked,
            }
        )

    for count, question in enumerate(questions, start=1):
        write(question)
        if mode == "isolated":
            query(question)
            started = time.perf_counter()
            retried(partial(target.empty, first, scope(run_id, question)), target)
            delete_s.append(time.perf_counter() - started)
        _progress("written" if mode == "shared" else "done", count, len(questions))
    if mode == "shared":
        for count, question in enumerate(questions, start=1):
            query(question)
            _progress("searched", count, len(questions))
    timings = {
        "write_s": _spread(write_s),
        "delete_s": _spread(delete_s),
        "first_search_ms": {
            name: round(values[0], 3) for name, values in search_ms.items() if values
        },
        "search_ms_after_first": {name: _spread(values[1:]) for name, values in search_ms.items()},
    }
    return rows, timings


def retried(call: Callable[[], object], target: Target, attempts: int = RETRIES) -> None:
    """Run `call`, and run it again when the connection closed without a response.

    The Hotdata SDK can drop a pooled connection on a long cloud load. A write is safe to
    run again, because a put with unchanged content writes nothing. `target.engine`
    counts the retries.
    """
    for attempt in range(1, attempts + 1):
        try:
            call()
            return
        except Exception as error:
            if attempt == attempts or DROPPED not in str(error):
                raise
            target.engine["retries"] = target.engine.get("retries", 0) + 1


def summarize(rows: Sequence[dict[str, Any]], ranking: str) -> dict[str, Any]:
    """Return the mean metrics of `ranking` over every row and over the rows of each type."""
    scored = [
        (row["type"], score([s for s, _ in row["ranked"][ranking]], row["evidence"], KS))
        for row in rows
    ]
    types = sorted({kind for kind, _ in scored})
    return {
        "questions": len(scored),
        "all": mean(metrics for _, metrics in scored),
        "by_type": {
            kind: {
                "questions": sum(1 for found, _ in scored if found == kind),
                **mean(metrics for found, metrics in scored if found == kind),
            }
            for kind in types
        },
    }


@contextmanager
def hotdata_target(
    embedder: Embedder, location: str, url: str, rankings: Sequence[str], run_id: str
) -> Iterator[Target]:
    """Yield the stores over a new database for this run, and delete the database after."""
    import pyarrow as pa
    from hotdata_framework import HotdataClient

    from bench.ablation import ABLATIONS, ArmStore
    from hotmemory.hotdata import EPISODE_TABLE, HotdataStore

    started = time.perf_counter()
    if location == "local":
        client = HotdataClient("local", "local", host=url)
    else:
        client = HotdataClient.from_env()
    client.list_managed_databases()
    first_call_s = round(time.perf_counter() - started, 3)
    provisioned = HotdataStore.provision(
        f"hotmemory-bench-{run_id}",
        embedder=embedder,
        model=embed.MODEL,
        dimensions=embed.DIMENSIONS,
        client=client,
    )
    options: dict[str, Any] = {
        "embedder": embedder,
        "model": embed.MODEL,
        "dimensions": embed.DIMENSIONS,
    }
    stores: dict[str, Store] = {
        name: (
            ArmStore(client, provisioned.database, arms=ABLATIONS[name], **options)
            if name in ABLATIONS
            else HotdataStore(client, provisioned.database, ranking=name, **options)  # type: ignore[arg-type]
        )
        for name in rankings
    }

    def empty(store: Store, namespace: Sequence[str]) -> None:
        assert isinstance(store, HotdataStore)
        path = "/".join(namespace).replace("'", "''")
        sql = f"SELECT id FROM default.public.{EPISODE_TABLE} WHERE namespace = '{path}'"
        ids = [row[0] for row in store.client.execute_sql(sql, database=store.database).rows]
        if ids:
            store._load(EPISODE_TABLE, pa.table({"id": ids}), "delete")

    engine = {
        "first_call_s": first_call_s,
        "framework": importlib.metadata.version("hotdata-framework"),
        "image": _local_image() if location == "local" else None,
    }
    try:
        yield Target("hotdata", location, stores, empty, engine)
        sql = f"SELECT count(*) FROM default.public.{EPISODE_TABLE}"
        engine["rows_at_end"] = client.execute_sql(sql, database=provisioned.database).rows[0][0]
    finally:
        client.delete_managed_database(provisioned.database)
        client.close()


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--driver", choices=("hotdata", "memory"), default="hotdata")
    parser.add_argument("--mode", choices=("isolated", "shared"), default="isolated")
    parser.add_argument("--ranking", choices=(*RANKINGS, "both"), default="both")
    parser.add_argument(
        "--ablate",
        action="store_true",
        help="also rank with the fused query less one arm at a time",
    )
    parser.add_argument("--location", choices=("local", "cloud"), default="local")
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--limit", type=int, default=None, help="the first N scored questions")
    args = parser.parse_args(argv)

    rankings = RANKINGS if args.ranking == "both" else (args.ranking,)
    if args.ablate:
        if args.driver == "memory":
            parser.error("--ablate needs --driver hotdata")
        rankings = (*rankings, *ABLATED)
    questions = [question for question in load(DATA_DIR / DATA_FILE) if question.is_scored]
    questions = questions[: args.limit]
    commit = _commit()
    started = datetime.now(UTC)
    run_id = f"{started:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    embedder = embed.openai_embedder()
    embedder([question.question for question in questions])
    clock_start = time.perf_counter()

    if args.driver == "memory":
        if rankings != ("vector",):
            parser.error("the memory driver ranks by vector only: pass --ranking vector")
        target = memory_target(embedder)
        rows, timings = run(questions, target, args.mode, run_id)
    else:
        with hotdata_target(embedder, args.location, args.url, rankings, run_id) as target:
            rows, timings = run(questions, target, args.mode, run_id)

    summaries = {name: summarize(rows, name) for name in rankings}
    result = {
        "config": {
            "benchmark": "LongMemEval-S",
            "data_file": DATA_FILE,
            "data_revision": DATA_REVISION,
            "data_sha256": DATA_SHA256,
            "granularity": "session, user turns only",
            "embedder": {
                "model": embed.MODEL,
                "dimensions": embed.DIMENSIONS,
                "max_input_tokens": embed.MAX_INPUT_TOKENS,
            },
            "driver": target.driver,
            "location": target.location,
            "engine": target.engine,
            "rankings": list(rankings),
            "mode": args.mode,
            "k": K,
            "ks": list(KS),
            "questions": len(questions),
            "limit": args.limit,
            "hotmemory_commit": commit,
            "run_id": run_id,
            "started_at": started.isoformat(),
            "duration_s": round(time.perf_counter() - clock_start, 1),
            "embedder_calls": embedder.calls,
            "embedder_misses": embedder.misses,
        },
        "summary": summaries,
        "timings": timings,
        "questions": rows,
    }
    embedder.close()
    RESULTS_DIR.mkdir(exist_ok=True)
    name = f"retrieval-{target.driver}-{target.location}-{args.mode}-{'-'.join(rankings)}"
    if args.limit is not None:
        name += f"-first{args.limit}"
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(json.dumps(result, indent=1) + "\n")
    for ranking in rankings:
        summary = summaries[ranking]["all"]
        print(
            f"{ranking}: recall_any@5 {summary['recall_any@5']:.4f}, "
            f"recall_all@5 {summary['recall_all@5']:.4f}, ndcg_any@10 {summary['ndcg_any@10']:.4f}"
        )
    print(f"wrote {path}")
    return path


def memory_target(embedder: Embedder) -> Target:
    """Return a `MemoryStore` target with a fixed clock, so ties in distance go by id."""
    store = MemoryStore(embedder=embedder, clock=lambda: FIXED_TIME)

    def empty(store: Store, namespace: Sequence[str]) -> None:
        for record in store.list(namespace, limit=10_000):
            store.delete(record.namespace, record.key)

    return Target("memory", "in-process", {"vector": store}, empty, {})


def _spread(values: Sequence[float]) -> dict[str, float]:
    """Return the count, the total, the maximum, and the nearest-rank p50, p95, and p99."""
    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def at(share: float) -> float:
        return round(ordered[max(0, math.ceil(share * len(ordered)) - 1)], 3)

    return {
        "count": len(ordered),
        "total": round(sum(ordered), 3),
        "p50": at(0.50),
        "p95": at(0.95),
        "p99": at(0.99),
        "max": round(ordered[-1], 3),
    }


def _progress(what: str, count: int, total: int) -> None:
    if count == total or count % 25 == 0:
        print(f"{what} {count}/{total}", flush=True)


def _commit() -> str:
    """Return the commit of the working tree, with `+dirty` if a tracked file changed."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD"], capture_output=True).returncode
    return head.stdout.strip() + ("+dirty" if dirty else "")


def _local_image() -> str | None:
    found = subprocess.run(
        ["docker", "compose", "images", "runtimedb", "--format", "json"],
        capture_output=True,
        text=True,
    )
    if found.returncode != 0:
        return None
    images = json.loads(found.stdout or "[]")
    return images[0].get("ID") if images else None


if __name__ == "__main__":
    main()
