"""The BEAM end-to-end score: capture each conversation once, then recall, read, and judge.

Run it from the root of the repository, with the local stack up. Without `--run`, it prints
the cost estimate and the expected time, and stops. With `--replay`, it calls no model:

    uv run --group bench --env-file .env python -m bench.beam_e2e --split 100K --sample smoke
    uv run --group bench --env-file .env python -m bench.beam_e2e --split 100K --sample a --run
    uv run --group bench --env-file .env python -m bench.beam_e2e --split 1M --sample all \\
        --workers 8 --run

Each conversation gets one local database that keeps its memory after the run, named
`<kept name>-<conversation id>`. A worker takes one conversation at a time, the one with the
most windows first. It captures each window in order with the `detail` extractor, then, for
each probe, recalls once for each arm, reads the block with each reader, and judges each
answer with the official BEAM scoring. The reads and the judge calls of a conversation run
in a thread pool.

It appends one row for each captured conversation and one row for each finished probe to a
rows file. `--resume` skips the finished probes and the captured conversations. A
conversation that stopped during capture keeps its database, and its capture runs again:
`capture` skips each window whose chunks are stored, with no extractor call. `--reuse`
reads from the kept databases with no capture. The runner deletes no database.
"""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import multiprocessing
import statistics
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench import embed
from bench.beam import (
    DATA_REVISION,
    SAMPLE_SPLIT,
    SAMPLES,
    SPLITS,
    WINDOW_CHARS,
    Conversation,
    Probe,
    Window,
    load_split,
    sample_ids,
)
from bench.beam_score import BEAM_COMMIT, EVENT_ORDERING, JUDGE, JUDGE_MAX_TOKENS, answer_prompt
from bench.beam_score import score as score_probe
from bench.beam_score import summarize as summarize_scores
from bench.e2e import (
    COST_LIMIT,
    Config,
    WriteTimer,
    append_row,
    existing_databases,
    extractor,
    kept_name,
    library_commit,
    local_store,
)
from bench.embed import CACHE_DIR
from bench.models import (
    BEAM_MINI,
    EMBEDDING_PRICE,
    EXTRACTOR_MAX_TOKENS,
    PRICES,
    CachedModels,
    Complete,
    Reply,
)
from bench.retrieval import RESULTS_DIR, _commit
from hotmemory import Filter, Memory, Store
from hotmemory.contract import render_episode

ARMS = ("memory", "chunks")
READERS = {"mini": BEAM_MINI}
READER_MAX_TOKENS = 2000
CONFIG = Config(
    k=30,
    budget=16_000,
    extractor="detail",
    session_episodes=True,
    episodes="search",
    chunk_chars=1000,
)
"""The phase 10 configuration of the memory arm."""
NAMESPACE = ("bench", "beam")
ACTOR = "beam-extractor"
JUDGE_THREADS = 8
SMOKE = "smoke"
ALL = "all"
SPEND_FACTOR = 2.0
WINDOW_KEY_CHARS = len("window-01-0001")


@dataclass(frozen=True)
class Rates:
    """The rates of the estimate. `docs/internal/plan.md` gives the derivation of each."""

    extract_window: float = 0.0005
    window_seconds: float = 8.3
    embed_tokens_per_char: float = 2.1 / 4.4
    reader_in: int = 3850
    reader_out: int = 80
    judge_in: int = 1200
    judge_out: int = 80
    match_calls: int = 85
    match_in: int = 150
    match_out: int = 2
    call_seconds: float = 0.7


@dataclass(frozen=True)
class Estimate:
    """The expected cost of a run in US dollars, by part, and its expected time in seconds."""

    parts: dict[str, float]
    by_conversation: dict[str, float]
    seconds: float

    @property
    def total(self) -> float:
        return sum(self.parts.values())


def estimate(
    conversations: Sequence[Conversation],
    arms: Sequence[str],
    readers: Sequence[str],
    workers: int,
    rates: Rates | None = None,
    capture: bool = True,
) -> Estimate:
    """Return the expected cost and time of a run over `conversations`.

    The time is the longest worker when each worker takes the conversation with the most
    windows first. A conversation takes its windows times `window_seconds` for capture,
    and its model calls of the read phase times `call_seconds`, over JUDGE_THREADS threads.
    """
    rates = rates or Rates()
    judge_in, judge_out = PRICES[JUDGE]
    nugget_call = (rates.judge_in * judge_in + rates.judge_out * judge_out) / 1e6
    match_call = (rates.match_in * judge_in + rates.match_out * judge_out) / 1e6
    parts = dict.fromkeys(("extraction", "embeddings", "readers", "judge"), 0.0)
    by_conversation: dict[str, float] = {}
    seconds: list[float] = []
    combos = len(arms) * len(readers)
    for conversation in conversations:
        windows = len(conversation.windows()) if capture else 0
        extraction = windows * rates.extract_window
        embeddings = (
            conversation.chars * rates.embed_tokens_per_char * EMBEDDING_PRICE / 1e6
            if capture
            else 0.0
        )
        reading = 0.0
        for reader in readers:
            price_in, price_out = PRICES[READERS[reader]]
            call = (rates.reader_in * price_in + rates.reader_out * price_out) / 1e6
            reading += call * len(arms) * len(conversation.probes)
        nuggets = sum(len(probe.rubric) for probe in conversation.probes)
        orderings = sum(1 for probe in conversation.probes if probe.ability == EVENT_ORDERING)
        judging = combos * (nuggets * nugget_call + orderings * rates.match_calls * match_call)
        parts["extraction"] += extraction
        parts["embeddings"] += embeddings
        parts["readers"] += reading
        parts["judge"] += judging
        by_conversation[conversation.id] = extraction + embeddings + reading + judging
        calls = combos * (len(conversation.probes) + nuggets + orderings * rates.match_calls)
        seconds.append(windows * rates.window_seconds + calls * rates.call_seconds / JUDGE_THREADS)
    return Estimate(parts, by_conversation, longest_worker(seconds, workers))


def longest_worker(seconds: Sequence[float], workers: int) -> float:
    """Return the time of the busiest worker when each takes the longest job left first."""
    loads = [0.0] * max(1, workers)
    for job in sorted(seconds, reverse=True):
        heapq.heapreplace(loads, loads[0] + job)
    return max(loads)


class SpendLimit(RuntimeError):
    """A conversation spent more than its limit."""


class ThreadModels:
    """A `CachedModels` for each thread, over one cache file, with a spend limit.

    `spent` and `calls` sum every thread. A call raises SpendLimit once `spent` passes
    `limit`, and makes no model call after that.
    """

    def __init__(self, complete: Complete, path: Path, limit: float = float("inf")) -> None:
        self._complete = complete
        self._path = path
        self._limit = limit
        self._local = threading.local()
        self._lock = threading.Lock()
        self._all: list[CachedModels] = []

    def __call__(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        schema: dict[str, Any] | None = None,
        system: str | None = None,
    ) -> Reply:
        models: CachedModels | None = getattr(self._local, "models", None)
        if models is None:
            models = CachedModels(self._complete, self._path)
            self._local.models = models
            with self._lock:
                self._all.append(models)
        self._check()
        reply = models(model, prompt, max_tokens, schema, system)
        self._check()
        return reply

    def _check(self) -> None:
        if self.spent > self._limit:
            raise SpendLimit(f"spent ${self.spent:.2f}, over the limit of ${self._limit:.2f}")

    @property
    def spent(self) -> float:
        with self._lock:
            return sum(models.spent for models in self._all)

    @property
    def calls(self) -> int:
        with self._lock:
            return sum(models.calls for models in self._all)

    def close(self) -> None:
        with self._lock:
            for models in self._all:
                models.close()


@dataclass(frozen=True)
class Block:
    """The block of one arm for one probe, and what it shows."""

    text: str
    episodes: tuple[str, ...]
    facts: int


def memory_block(store: Store, namespace: Sequence[str], question: str) -> Block:
    """Return the block of the memory arm: `recall` with the configuration of CONFIG."""
    records, text = Memory(store).recall(
        question, [namespace], budget=CONFIG.budget, k=CONFIG.k, episodes="search"
    )
    episodes = tuple(record.key for record in records if record.kind == "episode")
    return Block(text, episodes, len(records) - len(episodes))


def chunks_block(store: Store, namespace: Sequence[str], question: str) -> Block:
    """Return the block of the chunks arm: the top `k` chunks for `question`, in the budget.

    The chunks are rendered as `recall` renders episodes, and the block stops before the
    first chunk that would pass the budget.
    """
    hits = store.search(question, [namespace], Filter(kind="episode"), k=CONFIG.k)
    parts: list[str] = []
    shown: list[str] = []
    for hit in hits:
        more = [*parts, render_episode(hit.record)]
        if len("\n".join(more)) > CONFIG.budget:
            break
        parts, shown = more, [*shown, hit.record.key]
    return Block("\n".join(parts), tuple(shown), 0)


BLOCKS: dict[str, Callable[[Store, Sequence[str], str], Block]] = {
    "memory": memory_block,
    "chunks": chunks_block,
}


def source_windows(windows: Sequence[Window], probe: Probe) -> set[str]:
    """Return the keys of the windows that hold a source turn of `probe`."""
    sources = set(probe.sources)
    return {window.key for window in windows if sources & set(window.messages)}


def coverage(found: set[str], block: Block) -> float | None:
    """Return the share of the windows in `found` that the block shows a chunk of."""
    if not found:
        return None
    shown = {key[:WINDOW_KEY_CHARS] for key in block.episodes}
    return len(found & shown) / len(found)


@dataclass(frozen=True)
class Job:
    """What every worker of one run shares."""

    url: str
    arms: tuple[str, ...]
    readers: tuple[str, ...]
    replay: bool
    kept: str
    rows_file: Path


@dataclass
class Done:
    """What one conversation did: its finished probes, what it cost, and its failure."""

    conversation: str
    probes: int = 0
    spent: float = 0.0
    calls: int = 0
    embedder_misses: int = 0
    failure: str | None = None
    seconds: float = 0.0
    capture: dict[str, Any] = field(default_factory=dict)


def capture_conversation(
    store: Store, models: ThreadModels, windows: Sequence[Window], namespace: Sequence[str]
) -> dict[str, Any]:
    """Capture each window in order, and return what the capture did and how long it took."""
    memory = Memory(store)
    timer = WriteTimer(store)
    at_limit: list[str] = []
    seconds: list[float] = []
    written = 0
    for window in windows:
        started = time.perf_counter()
        ids = memory.capture(
            window.text,
            namespace,
            extractor(models, CONFIG.variant, "", at_limit),
            actor=ACTOR,
            observed_at=window.date,
            episode=window.key,
            chunk_chars=CONFIG.chunk_chars,
        )
        seconds.append(time.perf_counter() - started)
        written += len(ids)
    ordered = sorted(seconds)
    return {
        "windows": len(windows),
        "windows_at_limit": len(at_limit),
        "facts_written": written,
        "window_s": {
            "p50": round(statistics.median(ordered), 3) if ordered else None,
            "p95": round(ordered[int(len(ordered) * 0.95)], 3) if ordered else None,
            "highest": round(ordered[-1], 3) if ordered else None,
            "sum": round(sum(ordered), 1),
        },
        "writes": timer.take(),
    }


def table_rows(store: Store) -> dict[str, int]:
    """Return the row count of each record table of a HotdataStore."""
    from hotmemory.hotdata import CUE_TABLE, RECORD_TABLES

    client, database = store.client, store.database  # type: ignore[attr-defined]
    counts = {}
    for table in (*RECORD_TABLES, CUE_TABLE):
        result = client.execute_sql(
            f"SELECT COUNT(*) FROM default.public.{table}", database=database
        )
        counts[table] = int(result.rows[0][0])
    return counts


def answer_and_score(
    models: ThreadModels, probe: Probe, block: Block, reader: str
) -> dict[str, Any]:
    """Read `block` with `reader`, judge the answer, and return the cell of the row."""
    reply = models(READERS[reader], answer_prompt(block.text, probe.question), READER_MAX_TOKENS)
    found = score_probe(models, probe, reply.text)
    return {
        "answer": reply.text,
        "reader_input_tokens": reply.input_tokens,
        "reader_output_tokens": reply.output_tokens,
        "score": found.score,
        "nugget": found.nugget,
        "nuggets": list(found.nuggets),
        "tau_norm": found.tau_norm,
        "f1": found.f1,
        "tau_undefined": found.tau_undefined,
    }


def run_on_store(
    job: Job,
    conversation: Conversation,
    store: Store,
    models: ThreadModels,
    capture: bool,
    finished: frozenset[str],
    count_rows: Callable[[Store], dict[str, int]] | None = None,
) -> Done:
    """Capture `conversation` into `store` if `capture`, then run each probe not in `finished`.

    Appends a capture row and each probe row to `job.rows_file`. With `count_rows`, the
    capture row holds the row count of each table after the capture.
    """
    started = time.perf_counter()
    done = Done(conversation.id)
    namespace = (*NAMESPACE, conversation.id)
    windows = conversation.windows()
    if capture:
        done.capture = capture_conversation(store, models, windows, namespace)
        if count_rows is not None:
            done.capture["rows"] = count_rows(store)
        done.capture["seconds"] = round(time.perf_counter() - started, 1)
        append_row(
            job.rows_file, {"kind": "capture", "conversation": conversation.id, **done.capture}
        )
        print(
            f"{conversation.id} captured {len(windows)} windows, "
            f"{done.capture['windows_at_limit']} at the limit, "
            f"rows {done.capture.get('rows')}, spent ${models.spent:.2f}",
            flush=True,
        )
    probes = [probe for probe in conversation.probes if probe.id not in finished]
    blocks = {
        (probe.id, arm): BLOCKS[arm](store, namespace, probe.question)
        for probe in probes
        for arm in job.arms
    }
    tasks = [(probe, arm, reader) for probe in probes for arm in job.arms for reader in job.readers]
    cells: dict[tuple[str, str, str], dict[str, Any]] = {}
    with ThreadPoolExecutor(JUDGE_THREADS) as pool:
        futures = {
            pool.submit(answer_and_score, models, probe, blocks[(probe.id, arm)], reader): (
                probe.id,
                arm,
                reader,
            )
            for probe, arm, reader in tasks
        }
        for future in as_completed(futures):
            cells[futures[future]] = future.result()
    for probe in probes:
        append_row(job.rows_file, probe_row(conversation, probe, windows, blocks, cells, job))
        done.probes += 1
    print(f"{conversation.id} finished {len(probes)} probes, spent ${models.spent:.2f}", flush=True)
    return done


def probe_row(
    conversation: Conversation,
    probe: Probe,
    windows: Sequence[Window],
    blocks: Mapping[tuple[str, str], Block],
    cells: Mapping[tuple[str, str, str], dict[str, Any]],
    job: Job,
) -> dict[str, Any]:
    """Return the row of the result file for one probe."""
    found = source_windows(windows, probe)
    arms = {}
    for arm in job.arms:
        block = blocks[(probe.id, arm)]
        arms[arm] = {
            "context_chars": len(block.text),
            "block_sha256": hashlib.sha256(block.text.encode()).hexdigest(),
            "facts_in_block": block.facts,
            "episodes_in_block": list(block.episodes),
            "source_windows": sorted(found),
            "source_coverage": coverage(found, block),
        }
    return {
        "kind": "probe",
        "id": probe.id,
        "conversation": conversation.id,
        "domain": conversation.domain,
        "ability": probe.ability,
        "arms": arms,
        "combos": {
            f"{arm}/{reader}": cells[(probe.id, arm, reader)]
            for arm in job.arms
            for reader in job.readers
        },
    }


def run_conversation(
    job: Job,
    conversation: Conversation,
    limit: float,
    capture: bool,
    finished: frozenset[str],
) -> Done:
    """Run `conversation` over its kept local database, as `run_on_store` does.

    Returns every error as the text of `failure`, so that no exception crosses the process
    pool. The database is kept in every case.
    """
    from bench.models import Offline, Remote

    started = time.perf_counter()
    done = Done(conversation.id)
    models = ThreadModels(
        Offline() if job.replay else Remote(), CACHE_DIR / "replies.sqlite", limit
    )
    embedder = embed.openai_embedder()
    try:
        name = f"{job.kept}-{conversation.id}"
        with local_store(job.url, name, embedder, "never") as (stores, _):
            store = stores[CONFIG.recall_with]
            done = run_on_store(job, conversation, store, models, capture, finished, table_rows)
    except Exception as error:
        done.failure = f"{conversation.id}: {type(error).__name__}: {error}"
        print(f"{conversation.id} failed: {done.failure}", flush=True)
    finally:
        done.spent, done.calls = models.spent, models.calls
        done.embedder_misses = embedder.misses
        done.seconds = round(time.perf_counter() - started, 1)
        models.close()
        embedder.close()
    return done


def read_rows(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return the header, the capture rows, and the probe rows of a rows file.

    A last line that is not complete JSON is left out.
    """
    lines = path.read_text().splitlines()
    header: dict[str, Any] = json.loads(lines[0])["header"]
    captures: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    for line in lines[1:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        (captures if row["kind"] == "capture" else probes).append(row)
    return header, captures, probes


def summarize(
    rows: Sequence[Mapping[str, Any]], arms: Sequence[str], readers: Sequence[str]
) -> dict[str, Any]:
    """Return the scores of each arm and reader over `rows`, the probe rows of a run."""
    return {
        f"{arm}/{reader}": summarize_scores(
            [
                {
                    "ability": row["ability"],
                    "domain": row["domain"],
                    "score": row["combos"][f"{arm}/{reader}"]["score"],
                }
                for row in rows
            ]
        )
        for arm in arms
        for reader in readers
    }


def choose(split: str, sample: str, conversations: Sequence[Conversation]) -> list[Conversation]:
    """Return the conversations of `sample` in `split`, in the order of the split.

    The smoke sample is the conversation of sample A with the fewest windows.
    """
    if sample == ALL:
        return list(conversations)
    if split != SAMPLE_SPLIT:
        raise ValueError(f"sample {sample} needs --split {SAMPLE_SPLIT}")
    ids = set(sample_ids("a" if sample == SMOKE else sample, conversations))
    chosen = [conversation for conversation in conversations if conversation.id in ids]
    if sample == SMOKE:
        chosen = [min(chosen, key=lambda c: len(c.windows()))]
    return chosen


def main(argv: Sequence[str] | None = None) -> Path | None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--split", choices=sorted(SPLITS), default=SAMPLE_SPLIT)
    parser.add_argument("--sample", choices=(*SAMPLES, SMOKE, ALL), default=SMOKE)
    parser.add_argument("--arms", default=",".join(ARMS), help="a comma-separated list of arms")
    parser.add_argument(
        "--readers", default=",".join(READERS), help="a comma-separated list of readers"
    )
    parser.add_argument(
        "--run", action="store_true", help="call the models; without it, only estimate"
    )
    parser.add_argument(
        "--replay", action="store_true", help="call no model: use cached replies, fail at a miss"
    )
    parser.add_argument(
        "--reuse", action="store_true", help="read from the kept databases, with no capture"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip what the rows file of an earlier run of this command holds",
    )
    parser.add_argument("--workers", type=int, default=1, help="conversations at a time")
    parser.add_argument("--limit", type=int, default=None, help="the first N conversations")
    parser.add_argument(
        "--extract-rate",
        type=float,
        default=Rates.extract_window,
        help="US dollars of extraction for each window, for the estimate",
    )
    parser.add_argument(
        "--window-seconds",
        type=float,
        default=Rates.window_seconds,
        help="worker seconds for each window, for the estimate",
    )
    parser.add_argument("--tag", default="", help="a suffix for the name of the result file")
    parser.add_argument("--url", default="http://localhost:3000")
    args = parser.parse_args(argv)
    arms = tuple(arm for arm in args.arms.split(",") if arm)
    readers = tuple(reader for reader in args.readers.split(",") if reader)
    if not arms or not set(arms) <= set(ARMS):
        parser.error(f"arms must be among {', '.join(ARMS)}")
    if not readers or not set(readers) <= set(READERS):
        parser.error(f"readers must be among {', '.join(READERS)}")
    if args.workers < 1:
        parser.error("workers must be at least 1")
    try:
        conversations = choose(args.split, args.sample, load_split(args.split))
    except ValueError as error:
        parser.error(str(error))
    conversations = conversations[: args.limit]

    rates = Rates(extract_window=args.extract_rate, window_seconds=args.window_seconds)
    expected = estimate(conversations, arms, readers, args.workers, rates, not args.reuse)
    for part, cost in expected.parts.items():
        print(f"estimate {part}: ${cost:.2f}")
    print(
        f"estimate in total, {len(conversations)} conversations, "
        f"{sum(len(c.probes) for c in conversations)} probes: ${expected.total:.2f}, "
        f"and the limit is ${COST_LIMIT:.0f}"
    )
    print(
        f"expected time with {args.workers} workers: {expected.seconds / 60:.0f} minutes; "
        f"a conversation stops at {SPEND_FACTOR:g} times its estimate"
    )
    if expected.total > COST_LIMIT:
        print("the estimate is over the limit: stop")
        return None
    if not args.run and not args.replay:
        print("no model was called; pass --run to run, or --replay to run from the cache")
        return None

    try:
        kept = kept_name(f"beam-{args.split.lower()}", CONFIG, library_commit())
    except RuntimeError as error:
        parser.error(str(error))
    name = f"beam-{args.split}-{args.sample}"
    name += f"-{args.tag}" if args.tag else ""
    name += f"-first{args.limit}" if args.limit is not None else ""
    path = RESULTS_DIR / f"{name}.json"
    rows_file = RESULTS_DIR / f"{name}.rows.jsonl"
    header = {
        "split": args.split,
        "sample": args.sample,
        "limit": args.limit,
        "arms": list(arms),
        "readers": list(readers),
        "config": asdict(CONFIG),
        "kept": kept,
        "reuse": args.reuse,
    }
    captured: set[str] = set()
    finished: set[str] = set()
    if args.resume:
        if not rows_file.exists():
            parser.error(f"--resume found no rows file {rows_file}")
        found_header, earlier_captures, earlier_probes = read_rows(rows_file)
        if found_header != header:
            parser.error(f"the rows file {rows_file} is of another run: {found_header}")
        captured = {row["conversation"] for row in earlier_captures}
        finished = {row["id"] for row in earlier_probes}
    elif rows_file.exists():
        parser.error(f"the rows file {rows_file} exists: pass --resume, or delete it")
    left = [c for c in conversations if any(p.id not in finished for p in c.probes)]
    if args.resume:
        print(f"resuming: {len(finished)} probes finished, {len(left)} conversations left")

    names = [f"{kept}-{conversation.id}" for conversation in conversations]
    found = existing_databases(args.url, names)
    if args.reuse:
        missing = sorted(set(names) - set(found))
        if missing:
            parser.error(f"{len(missing)} kept databases do not exist, for example {missing[0]}")
    elif found and not args.resume:
        parser.error(
            f"{len(found)} kept databases exist already, for example {found[0]}: "
            "pass --reuse to read from them, --resume to go on, or delete them"
        )
    print(f"{'reusing' if args.reuse else 'keeping'} the memory as {kept}-<conversation id>")

    commit = _commit()
    started = datetime.now(UTC)
    run_id = f"{started:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    clock_start = time.perf_counter()
    RESULTS_DIR.mkdir(exist_ok=True)
    if not args.resume:
        rows_file.write_text(json.dumps({"header": header}) + "\n")
    job = Job(args.url, arms, readers, args.replay, kept, rows_file)
    order = sorted(left, key=lambda c: len(c.windows()), reverse=True)
    frozen = frozenset(finished)
    calls = [
        (
            job,
            conversation,
            SPEND_FACTOR * expected.by_conversation[conversation.id],
            not args.reuse and conversation.id not in captured,
            frozen,
        )
        for conversation in order
    ]
    workers = min(args.workers, len(calls)) if calls else 0
    if workers <= 1:
        done = [run_conversation(*call) for call in calls]
    else:
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(workers, mp_context=context) as pool:
            done = list(pool.map(run_conversation, *zip(*calls, strict=True)))
    spent = sum(found_done.spent for found_done in done)
    failed = [found_done.failure for found_done in done if found_done.failure]
    if failed:
        print(f"{len(failed)} conversations failed; spent ${spent:.2f}")
        for failure in failed:
            print(f"  {failure}")
        print(f"the finished rows are in {rows_file}; run the command again with --resume")
        return None

    _, captures, probe_rows = read_rows(rows_file)
    by_id = {row["id"]: row for row in probe_rows}
    rows = [by_id[probe.id] for conversation in conversations for probe in conversation.probes]
    capture_of = {row["conversation"]: row for row in captures}
    summaries = summarize(rows, arms, readers)
    data_name, data_sha256 = SPLITS[args.split]
    result_file = {
        "config": {
            "benchmark": "BEAM",
            "split": args.split,
            "data_file": data_name,
            "data_revision": DATA_REVISION,
            "data_sha256": data_sha256,
            "sample": (
                {"name": ALL, "conversations": len(conversations)}
                if args.sample == ALL
                else {
                    "name": args.sample,
                    "file": SAMPLES["a" if args.sample == SMOKE else args.sample].file,
                    "seed": SAMPLES["a" if args.sample == SMOKE else args.sample].seed,
                    "conversations": [c.id for c in conversations],
                }
            ),
            "readers": {
                reader: {
                    "model": READERS[reader],
                    "temperature": 0,
                    "max_tokens": READER_MAX_TOKENS,
                    "prompt": f"answer_generation_for_rag, BEAM {BEAM_COMMIT}",
                }
                for reader in readers
            },
            "judge": {
                "model": JUDGE,
                "temperature": 0,
                "max_tokens": JUDGE_MAX_TOKENS,
                "prompt": f"unified_llm_judge_base_prompt, BEAM {BEAM_COMMIT}",
                "event_ordering": "tau_norm of the official line match",
                "question_shown": False,
            },
            "extractor": {
                "variant": CONFIG.extractor,
                "model": CONFIG.variant.model,
                "max_tokens": EXTRACTOR_MAX_TOKENS,
            },
            "embedder": {"model": embed.MODEL, "dimensions": embed.DIMENSIONS},
            "driver": "hotdata",
            "location": "local",
            "capture": {
                "window_chars": WINDOW_CHARS,
                "chunk_chars": CONFIG.chunk_chars,
                "kept": kept,
                "reused": args.reuse,
            },
            "recall": {
                "ranking": CONFIG.recall_with,
                "k": CONFIG.k,
                "budget": CONFIG.budget,
                "episodes": CONFIG.episodes,
                "as_of": None,
            },
            "arms": {
                "memory": "recall of facts, then chunks found by search, in the budget",
                "chunks": f"the top {CONFIG.k} chunks by vector search, in the budget",
            },
            "conversations": len(conversations),
            "probes": len(rows),
            "estimate_usd": round(expected.total, 2),
            "estimate_s": round(expected.seconds),
            "replay": args.replay,
            "workers": workers,
            "spent_usd": round(spent, 2),
            "resumed_probes": len(finished),
            "model_calls": sum(found_done.calls for found_done in done),
            "embedder_misses": sum(found_done.embedder_misses for found_done in done),
            "windows_at_limit": sum(row["windows_at_limit"] for row in captures),
            "hotmemory_commit": commit,
            "run_id": run_id,
            "started_at": started.isoformat(),
            "duration_s": round(time.perf_counter() - clock_start, 1),
        },
        "summary": summaries,
        "captures": [capture_of[c.id] for c in conversations if c.id in capture_of],
        "probes": rows,
    }
    path.write_text(json.dumps(result_file, indent=1) + "\n")
    rows_file.unlink()
    for combo, found_summary in summaries.items():
        print(
            f"{combo}: mean over abilities {found_summary['mean']:.3f}, "
            f"over questions {found_summary['questions']:.3f}"
        )
    print(f"spent ${spent:.2f}; wrote {path}")
    return path


if __name__ == "__main__":
    main()
