"""The scale test: one store filled with real chunks, timed and checked at each size.

Run it from the root of the repository, with the local stack up:

    uv run --group bench --env-file .env python -m bench.scale --sizes 10000

The store holds the sessions of LongMemEval-S as chunks of 1,000 characters, one
session after another, with no copy of a row. The evidence sessions of samples A and B
come first, so every size holds them. Each session goes into one of 100 buckets, under
the namespace `scale/<tens>/<bucket>`, so a scope of one bucket holds about 1 percent of
the rows and a scope of one tens label about 10 percent.

At each size in `--sizes`, the run searches each scored question of the two samples in
each scope, by each ranking, and records the time of each search. For the vector
ranking, it compares the top k with a brute-force ranking in numpy over the same rows.
Over the whole store, it scores `recall_any@k` against the evidence sessions. Each flush
records its time and the time of the read of the current revisions before it. It writes
one JSON file into `bench/results/`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from bench import embed, prompts
from bench.data import DATA_DIR, DATA_FILE, Question, Session, load
from bench.e2e import episode_key, sample_ids
from bench.episodes import ACTOR, session_of
from bench.metrics import score
from bench.retrieval import RESULTS_DIR, _commit
from hotmemory import Embedder, Filter, Store
from hotmemory._chunks import chunk_text

CHUNK_CHARS = 1000
BUCKETS = 100
FLUSH = 1000
K = 10
KS = (5, 10)
ROOT = ("scale",)
TIE = 1e-5
EPISODES = Filter(kind="episode")
FLUSH_ATTEMPTS = 3
TRANSIENT = ("upload transfer to storage failed", "Remote end closed connection")


@dataclass(frozen=True)
class Row:
    """One chunk of a session, with its key, namespace, and session id."""

    namespace: tuple[str, ...]
    key: str
    content: str
    session: str


def bucket(session_id: str) -> int:
    """Return the bucket of a session, from 0 to BUCKETS - 1, from the SHA-256 of its id."""
    return int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16) % BUCKETS


def namespace_of(number: int) -> tuple[str, ...]:
    """Return the namespace of bucket `number`: `scale/<tens>/<two digits>`."""
    return (*ROOT, str(number // 10), f"{number:02d}")


def scopes() -> dict[str, tuple[str, ...]]:
    """Return the three scopes: one bucket, one tens label, and the whole store."""
    return {"1%": namespace_of(0), "10%": (*ROOT, "0"), "100%": ROOT}


def pool(questions: Sequence[Question], first: Sequence[Question]) -> list[Session]:
    """Return each session of `questions` once, in three groups, each sorted by id.

    First the evidence sessions of `first`, then the other sessions of `first`, then the
    rest. A session with no turns is left out.
    """
    seen: dict[str, Session] = {}
    for question in (*first, *questions):
        for session in question.sessions:
            if session.turns and session.id not in seen:
                seen[session.id] = session
    evidence = sorted({i for q in first for i in q.evidence if i in seen})
    near = sorted({s.id for q in first for s in q.sessions if s.id in seen} - set(evidence))
    rest = sorted(set(seen) - set(evidence) - set(near))
    return [seen[i] for i in (*evidence, *near, *rest)]


def rows_of(sessions: Sequence[Session]) -> Iterator[Row]:
    """Yield the chunks of each session in order, as `capture` stores them in phase 10."""
    for session in sessions:
        namespace = namespace_of(bucket(session.id))
        key = episode_key(session.id)
        for number, chunk in enumerate(chunk_text(prompts.session_text(session), CHUNK_CHARS), 1):
            yield Row(namespace, f"{key}-{number:04d}", chunk, session.id)


def spread(values: Sequence[float]) -> dict[str, float]:
    """Return the count, p50, p95, and highest of `values`, rounded to 0.001."""
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return {
        "count": len(values),
        "p50": round(statistics.median(ordered), 3),
        "p95": round(p95, 3),
        "highest": round(ordered[-1], 3),
    }


def in_scope(row: Row, prefix: tuple[str, ...]) -> bool:
    return row.namespace[: len(prefix)] == prefix


class Exact:
    """The brute-force cosine ranking over every row written so far, in numpy."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.vectors: Any = None
        self.rows: list[Row] = []

    def add(self, rows: Sequence[Row], vectors: Sequence[Sequence[float]]) -> None:
        start = len(self.rows)
        block = np.asarray(vectors, dtype=np.float32)
        if self.vectors is None:
            self.vectors = np.zeros((self.capacity, block.shape[1]), dtype=np.float32)
        norms = np.linalg.norm(block, axis=1, keepdims=True)
        self.vectors[start : start + len(rows)] = block / np.where(norms == 0, 1, norms)
        self.rows.extend(rows)

    def scope(self, prefix: tuple[str, ...]) -> Any:
        """Return the positions of the rows under `prefix`."""
        mask = np.fromiter((in_scope(row, prefix) for row in self.rows), bool, len(self.rows))
        return np.flatnonzero(mask)

    def top(self, query: Sequence[float], index: Any, k: int) -> list[tuple[str, float]]:
        """Return the keys and distances of the k nearest rows among the positions `index`."""
        q = np.asarray(query, dtype=np.float32)
        q = q / (np.linalg.norm(q) or 1.0)
        distances = 1.0 - self.vectors[index] @ q
        order = np.argsort(distances, kind="stable")[:k]
        return [(self.rows[index[i]].key, float(distances[i])) for i in order]


def same_top(found: Sequence[tuple[str, float]], exact: Sequence[tuple[str, float]]) -> str:
    """Return `equal`, `ties` if the keys differ only among equal distances, or `differ`."""
    if [key for key, _ in found] == [key for key, _ in exact]:
        return "equal"
    if len(found) == len(exact) and all(
        abs(a - b) <= TIE for (_, a), (_, b) in zip(found, exact, strict=True)
    ):
        return "ties"
    return "differ"


class Run:
    """The fill and the checkpoints of one scale run over `stores`, one per ranking."""

    def __init__(
        self,
        stores: dict[str, Store],
        embedder: Embedder,
        questions: Sequence[Question],
        capacity: int,
    ) -> None:
        self.stores = stores
        self.first = next(iter(stores.values()))
        self.embedder = embedder
        self.questions = questions
        self.exact = Exact(capacity)
        self.flushes: list[dict[str, float]] = []
        self.retries = 0
        self.reads: list[float] = []
        read = getattr(self.first, "_current", None)
        if read is not None:

            def timed_read(slots: Any) -> Any:
                started = time.perf_counter()
                try:
                    return read(slots)
                finally:
                    self.reads.append(time.perf_counter() - started)

            self.first._current = timed_read  # type: ignore[attr-defined]

    def fill(self, rows: Sequence[Row]) -> None:
        """Write `rows` in flushes of FLUSH rows, and time each flush."""
        for start in range(0, len(rows), FLUSH):
            part = rows[start : start + FLUSH]
            vectors = self.embedder([row.content for row in part])
            started = time.perf_counter()
            for attempt in range(1, FLUSH_ATTEMPTS + 1):
                try:
                    self.write(part)
                    break
                except RuntimeError as error:
                    if attempt == FLUSH_ATTEMPTS or not any(t in str(error) for t in TRANSIENT):
                        raise
                    self.retries += 1
                    print(f"flush failed, try {attempt + 1}: {error}", flush=True)
            seconds = time.perf_counter() - started
            read = self.reads[-1] if self.reads else 0.0
            self.exact.add(part, vectors)
            total = len(self.exact.rows)
            self.flushes.append({"rows": len(part), "total": total, "s": seconds, "read_s": read})
            print(f"wrote {total} rows, flush {seconds:.2f} s, read {read:.2f} s", flush=True)

    def write(self, part: Sequence[Row]) -> None:
        """Put `part` in one flush. A put of a row that is already current writes nothing."""
        with self.first.writer(max_rows=len(part) + 1, interval=timedelta.max) as writer:
            for row in part:
                writer.put(
                    row.namespace,
                    row.key,
                    kind="episode",
                    content=row.content,
                    sources=(f"session:{row.session}",),
                    actor=ACTOR,
                )

    def checkpoint(self) -> dict[str, Any]:
        """Search each question in each scope by each ranking, and return the results."""
        result: dict[str, Any] = {"rows": len(self.exact.rows), "scopes": {}}
        for name, prefix in scopes().items():
            index = self.exact.scope(prefix)
            entry: dict[str, Any] = {"rows": len(index)}
            for ranking, store in self.stores.items():
                times: list[float] = []
                checks = {"equal": 0, "ties": 0, "differ": 0}
                scored: list[dict[str, float]] = []
                for question in self.questions:
                    started = time.perf_counter()
                    hits = store.search(question.question, [prefix], EPISODES, k=K)
                    times.append((time.perf_counter() - started) * 1000)
                    found = [(hit.record.key, hit.distance or 0.0) for hit in hits]
                    if ranking == "vector":
                        query = self.embedder([question.question])[0]
                        checks[same_top(found, self.exact.top(query, index, K))] += 1
                    if name == "100%":
                        sessions = list(
                            dict.fromkeys(session_of(hit.record.sources) for hit in hits)
                        )
                        scored.append(score(sessions, question.evidence, KS))
                entry[ranking] = {"search_ms": spread(times)}
                if ranking == "vector":
                    entry[ranking]["exact"] = checks
                if scored:
                    entry[ranking]["metrics"] = {
                        f"recall_any@{k}": round(
                            sum(s[f"recall_any@{k}"] for s in scored) / len(scored), 4
                        )
                        for k in KS
                    }
            result["scopes"][name] = entry
        return result


def hotdata_stores(
    embedder: Embedder, location: str, url: str, name: str, vector_index: bool
) -> Any:
    """Return the client, the database, and one store for each ranking."""
    from hotdata_framework import HotdataClient

    from hotmemory.hotdata import HotdataStore

    if location == "local":
        client = HotdataClient("local", "local", host=url)
    else:
        client = HotdataClient.from_env()
    store = HotdataStore.provision(
        name,
        embedder=embedder,
        model=embed.MODEL,
        dimensions=embed.DIMENSIONS,
        client=client,
        vector_index=vector_index,
    )
    options: dict[str, Any] = {
        "embedder": embedder,
        "model": embed.MODEL,
        "dimensions": embed.DIMENSIONS,
    }
    stores: dict[str, Store] = {
        ranking: HotdataStore(
            client, store.database, ranking=ranking, vector_index=vector_index, **options
        )
        for ranking in ("vector", "fused")
    }
    return client, store.database, stores


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sizes", default="10000", help="comma-separated row counts")
    parser.add_argument("--location", choices=("local", "cloud"), default="local")
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--keep", action="store_true", help="keep the database after the run")
    parser.add_argument("--tag", default="")
    parser.add_argument(
        "--vector-index", action="store_true", help="provision with the experimental vector index"
    )
    args = parser.parse_args(argv)
    sizes = sorted(int(size) for size in args.sizes.split(","))

    questions = load(DATA_DIR / DATA_FILE)
    by_id = {question.id: question for question in questions}
    chosen = [by_id[i] for name in ("a", "b") for i in sample_ids(name, questions)]
    scored = [question for question in chosen if question.is_scored]
    rows = list(rows_of(pool(questions, chosen)))
    if sizes[-1] > len(rows):
        raise SystemExit(f"the pool holds {len(rows)} rows, fewer than {sizes[-1]}")

    embedder = embed.openai_embedder()
    run_id = uuid.uuid4().hex[:8]
    name = f"hotmemory-scale-{run_id}"
    client, database, stores = hotdata_stores(
        embedder, args.location, args.url, name, args.vector_index
    )
    run = Run(stores, embedder, scored, sizes[-1])
    started = datetime.now(UTC)
    checkpoints: list[dict[str, Any]] = []
    tag = f"-{args.tag}" if args.tag else ""
    path = RESULTS_DIR / f"scale-{args.location}{tag}-{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {
        "started": started.isoformat(),
        "location": args.location,
        "sizes": sizes,
        "pool_rows": len(rows),
        "questions": len(scored),
        "chunk_chars": CHUNK_CHARS,
        "flush": FLUSH,
        "k": K,
        "buckets": BUCKETS,
        "vector_index": args.vector_index,
        "commit": _commit(),
        "database": name if args.keep else None,
    }
    try:
        written = 0
        for size in sizes:
            run.fill(rows[written:size])
            written = size
            checkpoints.append(run.checkpoint())
            print(json.dumps(checkpoints[-1]), flush=True)
            config["flush_retries"] = run.retries
            write_result(path, config, run.flushes, sizes, checkpoints)
    finally:
        if not args.keep:
            client.delete_managed_database(database)
    print(path)
    return path


def write_result(
    path: Path,
    config: dict[str, Any],
    flushes: Sequence[dict[str, float]],
    sizes: Sequence[int],
    checkpoints: Sequence[dict[str, Any]],
) -> None:
    """Write the configuration, the flushes, and the checkpoints so far to `path`."""

    def by_size(field: str) -> dict[str, dict[str, float]]:
        return {
            str(size): spread([f[field] for f in flushes if prev < f["total"] <= size])
            for prev, size in zip([0, *sizes], sizes, strict=False)
        }

    result = {
        "config": config,
        "flushes": list(flushes),
        "flush_s_by_size": by_size("s"),
        "read_s_by_size": by_size("read_s"),
        "checkpoints": list(checkpoints),
    }
    path.write_text(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main()
