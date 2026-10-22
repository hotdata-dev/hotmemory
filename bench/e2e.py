"""The end-to-end score: three arms on the sample, with one reader and one judge.

Run it from the root of the repository. Without `--run`, it prints the cost estimate and
stops. With `--replay`, it calls no model, and it stops at the first reply that the cache
does not hold:

    uv run --group bench --env-file .env python -m bench.e2e
    uv run --group bench --env-file .env python -m bench.e2e --run
    uv run --group bench --env-file .env python -m bench.e2e --replay --arms memory
    uv run --group bench --env-file .env python -m bench.e2e --run --smoke --arms memory

With `--background <database>`, every question runs in that local database, which
`python -m bench.scale --prefill` filled, and the memory arm recalls over the namespace of
the question and the namespace `BACKGROUND`. Capture still reads and writes only the
namespace of the question. Each row records the time of each write of the memory arm.

With `--sample all`, it runs every question of the data file, in file order.

With `--smoke`, it runs the 18 questions of `bench/sample-smoke.json` on `MemoryStore`, one
store in each worker. With `--keep`, it keeps the memory of each question, and `--reuse`
recalls from it with no `capture`. It appends the row of each finished question to a rows
file, and `--resume` skips those rows. At the end, it writes one JSON file into
`bench/results/` with the configuration and the results.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import multiprocessing
import random
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from bench import embed, prompts
from bench.data import (
    DATA_DIR,
    DATA_FILE,
    DATA_REVISION,
    DATA_SHA256,
    QUESTION_TYPES,
    Question,
    Session,
    load,
)
from bench.embed import CACHE_DIR, Tokens
from bench.episodes import session_of, write_episodes
from bench.models import (
    EXTRACTOR_MAX_TOKENS,
    JUDGE,
    JUDGE_MAX_TOKENS,
    PRICES,
    READER,
    READER_MAX_TOKENS,
    CachedModels,
    CacheMiss,
    Reply,
)
from bench.retrieval import FIXED_TIME, RANKINGS, RESULTS_DIR, _commit
from hotmemory import Embedder, Fact, Filter, Memory, MemoryStore, Record, Store
from hotmemory.contract import Episodes

HERE = Path(__file__).resolve().parent
PER_TYPE = 9
ARMS = ("full", "retrieval", "memory")
RETRIEVED = 10
RECALL_K = 30
RECALL_BUDGET = 8000
EPISODES = ("none", "cited", "search")
KEPT_RUN = "kept"
COST_LIMIT = 50.0
EXTRACT_OVERHEAD = 700
ACTOR = "longmemeval-extractor"
BACKGROUND = ("background", "lme_s")
Models = Callable[..., Reply]


@dataclass(frozen=True)
class Config:
    """The configuration of the retrieval arm and the memory arm.

    `retrieval_ranking` ranks the sessions of the retrieval arm. The memory arm captures
    and recalls with `memory_ranking`, recalls `k` records into a block of at most
    `budget` characters, and extracts with the variant `extractor` of `prompts.VARIANTS`.
    With `fact_sources`, each fact carries the source of its session from `fact_source`.
    With `session_episodes`, `capture` stores each session as an episode under the key
    from `episode_key`, and the facts carry no session source. With `chunk_chars` as well,
    `capture` stores each session as chunks of at most that many characters. `episodes` is
    the value of `recall`, and needs `session_episodes` unless it is `none`.
    """

    retrieval_ranking: str = "vector"
    memory_ranking: str = "vector"
    k: int = RECALL_K
    budget: int = RECALL_BUDGET
    extractor: str = "baseline"
    fact_sources: bool = True
    session_episodes: bool = False
    episodes: str = "none"
    chunk_chars: int | None = None

    def __post_init__(self) -> None:
        for ranking in (self.retrieval_ranking, self.memory_ranking):
            if ranking not in RANKINGS:
                raise ValueError(f"ranking must be one of {', '.join(RANKINGS)}, got {ranking!r}")
        if self.extractor not in prompts.VARIANTS:
            raise ValueError(f"no extractor variant {self.extractor!r}")
        if self.k < 1 or self.budget < 1:
            raise ValueError("k and budget must be at least 1")
        if self.episodes not in EPISODES:
            raise ValueError(f"episodes must be one of {', '.join(EPISODES)}")
        if self.episodes != "none" and not self.session_episodes:
            raise ValueError("episodes needs session_episodes")
        if self.chunk_chars is not None and (self.chunk_chars < 1 or not self.session_episodes):
            raise ValueError("chunk_chars must be at least 1, and needs session_episodes")

    @property
    def variant(self) -> prompts.Variant:
        """Return the extractor variant."""
        return prompts.VARIANTS[self.extractor]


BASELINE = Config(fact_sources=False)
"""The configuration of the phase 5 run."""


@dataclass(frozen=True)
class Sample:
    """A fixed sample of questions: its file, its seed, and the samples it leaves out."""

    file: str
    seed: int
    excludes: tuple[str, ...] = ()


SAMPLES = {
    "a": Sample("bench/sample.json", 20261010),
    "b": Sample("bench/sample-b.json", 20261013, excludes=("a",)),
}
SMOKE = Sample("bench/sample-smoke.json", 20261016)
ALL = "all"
"""The name of the run over every question of the data file, in file order."""
SMOKE_PER_TYPE = 3


def choose_sample(
    questions: Sequence[Question],
    per_type: int = PER_TYPE,
    seed: int = SAMPLES["a"].seed,
    exclude: Collection[str] = (),
) -> list[str]:
    """Return `per_type` question ids of each type, drawn with `seed`, sorted by type and id.

    The draw leaves out the ids in `exclude`.
    """
    rng = random.Random(seed)
    left_out = set(exclude)
    chosen = []
    for kind in QUESTION_TYPES:
        ids = sorted(q.id for q in questions if q.type == kind and q.id not in left_out)
        chosen.extend(sorted(rng.sample(ids, per_type)))
    return chosen


def sample_ids(name: str, questions: Sequence[Question], root: Path = HERE.parent) -> list[str]:
    """Return the ids of the sample `name`, and write its file first if it does not exist.

    The draw of a sample leaves out every id of the samples that it excludes.
    """
    sample = SAMPLES[name]
    path = root / sample.file
    if not path.exists():
        exclude = {i for other in sample.excludes for i in sample_ids(other, questions, root)}
        chosen = choose_sample(questions, seed=sample.seed, exclude=exclude)
        path.write_text(json.dumps(chosen, indent=1) + "\n")
    ids: list[str] = json.loads(path.read_text())
    return ids


def smoke_ids(questions: Sequence[Question], root: Path = HERE.parent) -> list[str]:
    """Return the ids of the smoke sample, and write its file first if it does not exist.

    The smoke sample draws SMOKE_PER_TYPE questions of each type from sample A with the
    seed of SMOKE, sorted by type and id.
    """
    path = root / SMOKE.file
    if not path.exists():
        types = {question.id: question.type for question in questions}
        sample = sample_ids("a", questions, root)
        rng = random.Random(SMOKE.seed)
        chosen = []
        for kind in QUESTION_TYPES:
            of_kind = sorted(i for i in sample if types[i] == kind)
            chosen.extend(sorted(rng.sample(of_kind, SMOKE_PER_TYPE)))
        path.write_text(json.dumps(chosen, indent=1) + "\n")
    ids: list[str] = json.loads(path.read_text())
    return ids


def fact_source(session_id: str) -> str:
    """Return the source of the facts of a session: `session:` and 12 hex digits of its SHA-256.

    The reader sees the sources in the block, and an evidence session id holds `answer`, so
    the source does not show the id.
    """
    return "session:" + _session_hash(session_id)


CHUNK_SUFFIX = re.compile(r"(?<=^session-[0-9a-f]{12})-[0-9]{4}$")


def episode_key(session_id: str) -> str:
    """Return the key of the episode of a session: `session-` and the hash of `fact_source`."""
    return "session-" + _session_hash(session_id)


def session_key(key: str) -> str:
    """Return the episode key of the session of `key`: `key` without a chunk suffix `-<n>`."""
    return CHUNK_SUFFIX.sub("", key)


def _session_hash(session_id: str) -> str:
    """Return the first 12 hex digits of the SHA-256 of `session_id`."""
    return hashlib.sha256(session_id.encode()).hexdigest()[:12]


def extractor(
    models: Models,
    variant: prompts.Variant = prompts.VARIANTS["baseline"],
    source: str = "",
    at_limit: list[str] | None = None,
) -> Callable[[str, datetime | None, list[Record]], list[Fact]]:
    """Return the extractor callable of `capture`: one call to the variant's model per session.

    With `source`, each fact carries it in `sources`. A reply that reached the token limit
    gives no facts, and `source` is appended to `at_limit`.
    """
    sources = (source,) if source else ()

    def extract(text: str, observed_at: datetime | None, current: list[Record]) -> list[Fact]:
        date = observed_at.strftime("%Y/%m/%d (%a) %H:%M") if observed_at else "unknown"
        known = [f"- {record.content}" for record in current]
        prompt = prompts.extract(text, date, known, variant.prompt)
        reply = models(variant.model, prompt, EXTRACTOR_MAX_TOKENS, prompts.EXTRACT_SCHEMA)
        if reply.stopped:
            if at_limit is not None:
                at_limit.append(source)
            return []
        facts = json.loads(reply.text)["facts"]
        return [
            Fact(
                kind="fact",
                subject=item["subject"].strip(),
                content=item["content"].strip(),
                sources=sources,
            )
            for item in facts
            if item["content"].strip()
        ]

    return extract


@dataclass
class Arm:
    """The answer of one arm to one question, and what it cost."""

    context_chars: int
    answer: Reply
    verdict: Reply
    extracted: int | None = 0
    at_limit: int | None = 0
    episodes: tuple[str, ...] = ()
    block_sha256: str | None = None

    @property
    def correct(self) -> bool:
        return "yes" in self.verdict.text.lower()


def answer(
    models: Models,
    question: Question,
    prompt: str,
    context_chars: int,
    extracted: int | None = 0,
    at_limit: int | None = 0,
    episodes: tuple[str, ...] = (),
    block_sha256: str | None = None,
) -> Arm:
    """Read `prompt` with READER, judge the answer with JUDGE, and return the arm."""
    reply = models(READER, prompt, READER_MAX_TOKENS)
    verdict = models(JUDGE, prompts.judge(question, reply.text), JUDGE_MAX_TOKENS)
    return Arm(context_chars, reply, verdict, extracted, at_limit, episodes, block_sha256)


def run_question(
    question: Question,
    models: Models,
    stores: Mapping[str, Store],
    empty: Callable[[Sequence[str]], None],
    run_id: str,
    arms: Sequence[str] = ARMS,
    config: Config = BASELINE,
    capture: bool = True,
    background: Sequence[str] | None = None,
) -> dict[str, Arm]:
    """Run each arm on `question`, and empty its namespaces after.

    `stores` maps each ranking to a store, and every store holds the same records. Without
    `capture`, the memory arm recalls from the records that its namespace holds already,
    and its count of facts written and of sessions at the limit is None. With
    `background`, the final recall of the memory arm also reads that namespace.
    """
    result: dict[str, Arm] = {}
    if "full" in arms:
        prompt = prompts.read_chats(question, question.sessions)
        result["full"] = answer(models, question, prompt, len(prompt))
    if "retrieval" in arms:
        namespace = ("bench", "lme_s", run_id, "retrieval", question.id)
        store = stores[config.retrieval_ranking]
        write_episodes(store, namespace, question)
        hits = store.search(question.question, [namespace], Filter(kind="episode"), k=RETRIEVED)
        empty(namespace)
        by_id = {session.id: session for session in question.sessions}
        chosen = [by_id[session_of(hit.record.sources)] for hit in hits]
        prompt = prompts.read_chats(question, chosen)
        result["retrieval"] = answer(models, question, prompt, len(prompt))
    if "memory" in arms:
        namespace = ("bench", "lme_s", run_id, "memory", question.id)
        memory = Memory(stores[config.memory_ranking])
        written: int | None = None
        at_limit: list[str] = []
        if capture:
            written = 0
            for session in question.sessions:
                written += len(
                    capture_session(memory, namespace, session, models, config, at_limit)
                )
        records, block = memory.recall(
            question.question,
            [namespace] if background is None else [namespace, background],
            as_of=question.as_of,
            budget=config.budget,
            k=config.k,
            episodes=None if config.episodes == "none" else cast(Episodes, config.episodes),
        )
        empty(namespace)
        shown = tuple(record.key for record in records if record.kind == "episode")
        prompt = prompts.read_facts(question, block)
        result["memory"] = answer(
            models,
            question,
            prompt,
            len(block),
            written,
            len(at_limit) if capture else None,
            shown,
            hashlib.sha256(block.encode()).hexdigest(),
        )
    return result


def capture_session(
    memory: Memory,
    namespace: Sequence[str],
    session: Session,
    models: Models,
    config: Config,
    at_limit: list[str],
) -> list[str]:
    """Run `capture` on one session as the memory arm does, and return the fact ids.

    With `config.session_episodes`, the session is stored as an episode under
    `episode_key`, or as chunks of `config.chunk_chars` under keys that start with it.
    Else, with `config.fact_sources`, each fact carries `fact_source`.
    """
    if config.session_episodes:
        source, episode = "", episode_key(session.id)
    else:
        source, episode = (fact_source(session.id) if config.fact_sources else ""), None
    return memory.capture(
        prompts.session_text(session),
        namespace,
        extractor(models, config.variant, source, at_limit),
        actor=ACTOR,
        observed_at=session.date,
        episode=episode,
        chunk_chars=config.chunk_chars if episode is not None else None,
    )


def estimate(
    questions: Sequence[Question],
    tokens: Tokens,
    arms: Sequence[str] = ARMS,
    config: Config = BASELINE,
) -> dict[str, float]:
    """Return an upper estimate of the price of each arm in US dollars, from token counts.

    The full arm counts its prompts. The retrieval arm counts the RETRIEVED longest
    sessions of each question. The memory arm counts each session with EXTRACT_OVERHEAD
    tokens of prompt and 500 tokens of output, and a reader prompt of `config.budget`
    characters. Every reader reply counts READER_MAX_TOKENS, and every judge prompt 400
    tokens. The estimate does not subtract the replies that the cache holds.
    """
    reader_in, reader_out = PRICES[READER]
    extract_in, extract_out = PRICES[config.variant.model]
    read_out = READER_MAX_TOKENS * reader_out / 1e6
    judge = (400 * reader_in + JUDGE_MAX_TOKENS * reader_out) / 1e6
    cost = dict.fromkeys(arms, 0.0)
    for question in questions:
        if "full" in arms:
            prompt = tokens.count(prompts.read_chats(question, question.sessions))
            cost["full"] += prompt * reader_in / 1e6 + read_out + judge
        if "retrieval" in arms:
            longest = sorted(question.sessions, key=lambda s: len(prompts.session_text(s)))
            prompt = tokens.count(prompts.read_chats(question, longest[-RETRIEVED:]))
            cost["retrieval"] += prompt * reader_in / 1e6 + read_out + judge
        if "memory" in arms:
            for session in question.sessions:
                size = tokens.count(prompts.session_text(session)) + EXTRACT_OVERHEAD
                cost["memory"] += (size * extract_in + 500 * extract_out) / 1e6
            cost["memory"] += (config.budget // 3 + 300) * reader_in / 1e6 + read_out + judge
    return cost


def summarize(rows: Sequence[dict[str, Any]], arm: str) -> dict[str, Any]:
    """Return the accuracy of `arm` over every row and over the rows of each type."""
    marks = [(row["type"], row["arms"][arm]["correct"]) for row in rows if arm in row["arms"]]

    def accuracy(found: Sequence[bool]) -> dict[str, Any]:
        return {"questions": len(found), "correct": sum(found), "accuracy": sum(found) / len(found)}

    return {
        "all": accuracy([mark for _, mark in marks]),
        "by_type": {
            kind: accuracy([mark for found, mark in marks if found == kind])
            for kind in QUESTION_TYPES
            if any(found == kind for found, _ in marks)
        },
    }


Delete = Literal["always", "on_error", "never"]


@contextmanager
def local_store(
    url: str, name: str, embedder: Embedder | None = None, delete: Delete = "always"
) -> Iterator[tuple[dict[str, Store], Callable[[Sequence[str]], None]]]:
    """Yield a HotdataStore for each ranking, and a call that empties a namespace.

    The stores share the local database named `name`, which `provision` opens or creates.
    `delete` says when the database is deleted after the block: `always`, `on_error` when
    the block raises, or `never`. Without `embedder`, the stores embed with
    `embed.openai_embedder()`.
    """
    import pyarrow as pa
    from hotdata_framework import HotdataClient

    from hotmemory.hotdata import CUE_TABLE, RECORD_TABLES, HotdataStore

    client = HotdataClient("local", "local", host=url)
    embedder = embedder or embed.openai_embedder()
    store = HotdataStore.provision(
        name,
        embedder=embedder,
        model=embed.MODEL,
        dimensions=embed.DIMENSIONS,
        client=client,
    )

    def empty(namespace: Sequence[str]) -> None:
        path = "/".join(namespace).replace("'", "''")
        for table in RECORD_TABLES:
            sql = f"SELECT id FROM default.public.{table} WHERE namespace = '{path}'"
            ids = [row[0] for row in client.execute_sql(sql, database=store.database).rows]
            if ids:
                rows = pa.table({"id": ids})
                store._load_all([(table, rows, "delete"), (CUE_TABLE, rows, "delete")])

    stores: dict[str, Store] = {
        ranking: HotdataStore(
            client,
            store.database,
            embedder=embedder,
            model=embed.MODEL,
            dimensions=embed.DIMENSIONS,
            ranking=ranking,  # type: ignore[arg-type]
        )
        for ranking in RANKINGS
    }
    failed = True
    try:
        yield stores, empty
        failed = False
    finally:
        if delete == "always" or (delete == "on_error" and failed):
            client.delete_managed_database(store.database)
        client.close()


def existing_databases(url: str, names: Collection[str]) -> list[str]:
    """Return the names in `names` that a local database has, sorted."""
    from hotdata_framework import HotdataClient

    client = HotdataClient("local", "local", host=url)
    try:
        found = {database.description for database in client.list_managed_databases()}
    finally:
        client.close()
    return sorted(name for name in found & set(names) if name is not None)


def library_commit(root: Path = HERE.parent) -> str:
    """Return the last commit that changed `src/hotmemory`.

    Raises RuntimeError if `src/hotmemory` holds a change that is not committed, because
    then no commit names the code that wrote the memory.
    """
    changed = subprocess.run(
        ["git", "status", "--porcelain", "--", "src/hotmemory"],
        capture_output=True,
        text=True,
        cwd=root,
        check=True,
    )
    if changed.stdout.strip():
        raise RuntimeError("src/hotmemory has changes that are not committed")
    last = subprocess.run(
        ["git", "log", "-1", "--format=%h", "--", "src/hotmemory"],
        capture_output=True,
        text=True,
        cwd=root,
        check=True,
    )
    return last.stdout.strip()


def kept_name(sample: str, config: Config, commit: str) -> str:
    """Return the name of the kept memory of `sample` under `config` and the library `commit`.

    The name holds what decides the memory that `capture` writes: the sample, the extractor
    variant with 6 hex digits of the SHA-256 of its model and prompt, how the facts cite
    their session (`episodes`, `chunks<size>`, `sources`, or `plain`), the memory ranking,
    and the commit.
    The database of one question appends `-<question id>`.
    """
    variant = config.variant
    digest = hashlib.sha256(f"{variant.model}\n{variant.prompt}".encode()).hexdigest()[:6]
    if config.session_episodes:
        cites = "episodes" if config.chunk_chars is None else f"chunks{config.chunk_chars}"
    elif config.fact_sources:
        cites = "sources"
    else:
        cites = "plain"
    return (
        f"hotmemory-kept-{sample}-{config.extractor}{digest}-{cites}-"
        f"{config.memory_ranking}-{commit}"
    )


@dataclass(frozen=True)
class Job:
    """What every worker of one run shares."""

    url: str
    arms: tuple[str, ...]
    config: Config
    replay: bool
    total: int
    kept: str | None = None
    reuse: bool = False
    smoke: bool = False
    rows_file: Path | None = None
    background: str | None = None


@dataclass(frozen=True)
class Shard:
    """The questions of one worker, and the run id that names its database."""

    questions: list[Question]
    run_id: str


@dataclass(frozen=True)
class Done:
    """The rows of one shard, the questions that failed, and what its model calls cost."""

    rows: list[dict[str, Any]]
    spent: float
    calls: int
    embedder_misses: int
    failed: list[str]


def append_row(path: Path, row: dict[str, Any]) -> None:
    """Append `row` to the file at `path` as one JSON line, under a lock on the file."""
    with path.open("a") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        try:
            file.write(json.dumps(row) + "\n")
            file.flush()
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def read_rows(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the header and the rows of a rows file that `main` wrote.

    The first line is the header, and each later line is the row of one finished question.
    A last line that is not complete JSON is left out.
    """
    lines = path.read_text().splitlines()
    header: dict[str, Any] = json.loads(lines[0])["header"]
    rows = []
    for line in lines[1:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return header, rows


def shards_of(questions: Sequence[Question], workers: int, run_id: str) -> list[Shard]:
    """Deal `questions` over at most `workers` shards, in turn, so each shard gets every type.

    With one shard, the shard keeps `run_id`. With more, shard n gets `<run_id>-w<n>`.
    """
    count = max(1, min(workers, len(questions)))
    return [
        Shard(list(questions[n::count]), f"{run_id}-w{n}" if count > 1 else run_id)
        for n in range(count)
    ]


def row_of(question: Question, result: dict[str, Arm]) -> dict[str, Any]:
    """Return the row of the result file for one question."""
    return {
        "id": question.id,
        "type": question.type,
        "abstention": question.is_abstention,
        "arms": {
            arm: {
                "correct": found.correct,
                "context_chars": found.context_chars,
                "facts_written": found.extracted,
                "sessions_at_limit": found.at_limit,
                "episodes_in_block": list(found.episodes),
                "block_sha256": found.block_sha256,
                "reader_input_tokens": found.answer.input_tokens,
                "answer": found.answer.text,
                "verdict": found.verdict.text,
            }
            for arm, found in result.items()
        },
    }


class WriteTimer:
    """Records the seconds and the drafts of each `_write_many` call of a HotdataStore."""

    def __init__(self, store: Store) -> None:
        self.writes: list[tuple[int, float]] = []
        write = store._write_many  # type: ignore[attr-defined]

        def timed(drafts: Sequence[Any]) -> list[str]:
            started = time.perf_counter()
            try:
                ids: list[str] = write(drafts)
                return ids
            finally:
                self.writes.append((len(drafts), time.perf_counter() - started))

        store._write_many = timed  # type: ignore[attr-defined]

    def take(self) -> dict[str, Any]:
        """Return a summary of the writes since the last call, and forget them.

        The summary holds the count of writes, the count of drafts, and the p50, p95,
        highest, and sum of the seconds.
        """
        writes, self.writes = self.writes, []
        seconds = sorted(s for _, s in writes)
        if not seconds:
            return {"count": 0}
        return {
            "count": len(seconds),
            "drafts": sum(n for n, _ in writes),
            "p50_s": round(seconds[len(seconds) // 2], 3),
            "p95_s": round(seconds[min(len(seconds) - 1, int(len(seconds) * 0.95))], 3),
            "highest_s": round(seconds[-1], 3),
            "sum_s": round(sum(seconds), 3),
        }


def run_shard(job: Job, shard: Shard) -> Done:
    """Run the questions of `shard` in order, over a new local database of its own.

    With `job.kept`, each question runs over a new database named `<job.kept>-<question
    id>`, and the database keeps the memory of the question after the run. A question that
    raises deletes its database. With `job.reuse` as well, each question opens its kept
    database, skips `capture`, and never deletes the database. With `job.smoke`, the
    questions run over one `MemoryStore` of the worker, with a fixed clock. The worker
    opens its own model cache, embedder, and store, so it can run in its own process. It
    prints one line for each question, and appends the row of each finished question to
    `job.rows_file`. A question that raises is listed in `failed`, its namespaces are
    emptied, and the next question runs. CacheMiss stops the shard. With `job.background`,
    the questions run in that database, which is never deleted, and each row holds the
    writes of the memory arm and the time of the question.
    """
    from bench.models import Offline, Remote

    models = CachedModels(Offline() if job.replay else Remote(), CACHE_DIR / "replies.sqlite")
    embedder = embed.openai_embedder()
    rows: list[dict[str, Any]] = []
    failed: list[str] = []

    def finish(
        question: Question, result: dict[str, Arm], timing: dict[str, Any] | None = None
    ) -> None:
        rows.append(row_of(question, result))
        if timing is not None:
            rows[-1]["timing"] = timing
        if job.rows_file is not None:
            append_row(job.rows_file, rows[-1])
        marks = " ".join(
            f"{arm}={'yes' if found.correct else 'no'}" for arm, found in result.items()
        )
        print(
            f"{shard.run_id} {len(rows)}/{len(shard.questions)} of {job.total} "
            f"{question.id} {marks} spent ${models.spent:.2f}",
            flush=True,
        )

    def fail(question: Question, error: Exception) -> None:
        failed.append(f"{question.id}: {type(error).__name__}: {error}")
        print(f"{shard.run_id} {question.id} failed: {failed[-1]}", flush=True)

    try:
        if job.smoke:
            store = MemoryStore(embedder=embedder, clock=lambda: FIXED_TIME)

            def empty(namespace: Sequence[str]) -> None:
                for record in store.list(namespace, limit=100_000):
                    store.delete(record.namespace, record.key)

            in_process: dict[str, Store] = dict.fromkeys(RANKINGS, store)
            for question in shard.questions:
                try:
                    result = run_question(
                        question, models, in_process, empty, shard.run_id, job.arms, job.config
                    )
                except CacheMiss:
                    raise
                except Exception as error:
                    fail(question, error)
                    clean(empty, shard.run_id, question)
                    continue
                finish(question, result)
        elif job.background is not None:
            with local_store(job.url, job.background, embedder, "never") as (stores, emptied):
                timer = WriteTimer(stores[job.config.memory_ranking])
                for question in shard.questions:
                    started = time.perf_counter()
                    try:
                        result = run_question(
                            question,
                            models,
                            stores,
                            emptied,
                            shard.run_id,
                            job.arms,
                            job.config,
                            background=BACKGROUND,
                        )
                    except CacheMiss:
                        raise
                    except Exception as error:
                        timer.take()
                        fail(question, error)
                        clean(emptied, shard.run_id, question)
                        continue
                    timing = {
                        "writes": timer.take(),
                        "question_s": round(time.perf_counter() - started, 1),
                    }
                    finish(question, result, timing)
        elif job.kept is not None:
            delete: Delete = "never" if job.reuse else "on_error"
            for question in shard.questions:
                name = f"{job.kept}-{question.id}"
                try:
                    with local_store(job.url, name, embedder, delete) as (stores, _):
                        result = run_question(
                            question,
                            models,
                            stores,
                            _keep,
                            KEPT_RUN,
                            job.arms,
                            job.config,
                            capture=not job.reuse,
                        )
                except CacheMiss:
                    raise
                except Exception as error:
                    fail(question, error)
                    continue
                finish(question, result)
        else:
            name = f"hotmemory-bench-{shard.run_id}"
            with local_store(job.url, name, embedder) as (stores, emptied):
                for question in shard.questions:
                    try:
                        result = run_question(
                            question, models, stores, emptied, shard.run_id, job.arms, job.config
                        )
                    except CacheMiss:
                        raise
                    except Exception as error:
                        fail(question, error)
                        clean(emptied, shard.run_id, question)
                        continue
                    finish(question, result)
    finally:
        models.close()
        embedder.close()
    return Done(rows, models.spent, models.calls, embedder.misses, failed)


def clean(empty: Callable[[Sequence[str]], None], run_id: str, question: Question) -> None:
    """Empty the namespaces of the arms of `question` after it failed.

    Raises RuntimeError with the text of the error if a call fails, so that the error
    crosses a process pool.
    """
    try:
        for arm in ("retrieval", "memory"):
            empty(("bench", "lme_s", run_id, arm, question.id))
    except Exception as error:
        raise RuntimeError(
            f"could not empty the namespaces of {question.id}: {type(error).__name__}: {error}"
        ) from None


def _delete_databases(url: str, names: Collection[str]) -> None:
    """Delete each local database whose name is in `names`."""
    from hotdata_framework import HotdataClient

    client = HotdataClient("local", "local", host=url)
    try:
        for database in client.list_managed_databases():
            if database.description in names:
                client.delete_managed_database(database)
    finally:
        client.close()


def _keep(namespace: Sequence[str]) -> None:
    """Do nothing: a kept run leaves the namespace of each question in its database."""


def main(argv: Sequence[str] | None = None) -> Path | None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run", action="store_true", help="call the models; without it, only estimate"
    )
    parser.add_argument("--arms", default=",".join(ARMS), help="a comma-separated list of arms")
    parser.add_argument(
        "--limit", type=int, default=None, help="the first N questions of the sample"
    )
    parser.add_argument(
        "--sample",
        choices=(*SAMPLES, ALL),
        default="a",
        help="the sample to run, or all for every question",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="run the 18 questions of the smoke sample on MemoryStore, in each worker",
    )
    parser.add_argument("--retrieval-ranking", choices=RANKINGS, default="vector")
    parser.add_argument("--memory-ranking", choices=RANKINGS, default="vector")
    parser.add_argument("--recall-k", type=int, default=RECALL_K, help="k of the memory recall")
    parser.add_argument(
        "--recall-budget", type=int, default=RECALL_BUDGET, help="characters of the memory block"
    )
    parser.add_argument("--extractor", choices=tuple(prompts.VARIANTS), default="baseline")
    parser.add_argument(
        "--no-fact-sources",
        action="store_true",
        help="write the facts with no session source, as phases 5 and 6 did",
    )
    parser.add_argument(
        "--session-episodes",
        action="store_true",
        help="capture each session as an episode, and write the facts with no session source",
    )
    parser.add_argument(
        "--chunk-chars",
        type=int,
        default=None,
        help="with --session-episodes: store each session as chunks of this many characters",
    )
    parser.add_argument(
        "--episodes", choices=EPISODES, default="none", help="the episodes value of recall"
    )
    parser.add_argument(
        "--replay", action="store_true", help="call no model: use cached replies, stop at a miss"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="processes that run the questions, each over a database of its own",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the memory of each question in a local database of its own",
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="recall from the memory that a run with --keep kept, and skip capture",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="skip the questions that the rows file of an earlier run of this command holds",
    )
    parser.add_argument(
        "--background",
        default=None,
        help="a local database that bench.scale --prefill filled: run in it, and recall over it",
    )
    parser.add_argument("--tag", default="", help="a suffix for the name of the result file")
    parser.add_argument("--url", default="http://localhost:3000")
    args = parser.parse_args(argv)
    arms = tuple(arm for arm in args.arms.split(",") if arm)
    if not set(arms) <= set(ARMS):
        parser.error(f"arms must be among {', '.join(ARMS)}")
    if args.workers < 1:
        parser.error("workers must be at least 1")
    if args.keep and args.reuse:
        parser.error("pass --keep or --reuse, not both")
    if args.smoke and (args.keep or args.reuse or args.sample != "a"):
        parser.error("--smoke runs alone: no --keep, --reuse, or --sample")
    if args.background is not None and (args.keep or args.reuse or args.smoke):
        parser.error("--background runs alone: no --keep, --reuse, or --smoke")
    if args.background is not None and arms != ("memory",):
        parser.error("--background needs --arms memory")
    if (args.keep or args.reuse) and arms != ("memory",):
        parser.error(f"--{'keep' if args.keep else 'reuse'} needs --arms memory")
    try:
        config = Config(
            retrieval_ranking=args.retrieval_ranking,
            memory_ranking=args.memory_ranking,
            k=args.recall_k,
            budget=args.recall_budget,
            extractor=args.extractor,
            fact_sources=not args.no_fact_sources,
            session_episodes=args.session_episodes,
            episodes=args.episodes,
            chunk_chars=args.chunk_chars,
        )
    except ValueError as error:
        parser.error(str(error))
    if args.smoke:
        config = replace(config, retrieval_ranking="vector", memory_ranking="vector")

    all_questions = {question.id: question for question in load(DATA_DIR / DATA_FILE)}
    if args.smoke:
        sample = smoke_ids(list(all_questions.values()))
    elif args.sample == ALL:
        sample = list(all_questions)
    else:
        sample = sample_ids(args.sample, list(all_questions.values()))
    questions = [all_questions[question_id] for question_id in sample][: args.limit]

    tokens = embed.TiktokenTokens(encoding="o200k_base")
    cost = estimate(questions, tokens, arms, config)
    total = sum(cost.values())
    for arm in arms:
        print(f"estimate {arm}: ${cost[arm]:.2f}")
    print(
        f"estimate in total, {len(questions)} questions: ${total:.2f}, "
        f"and the limit is ${COST_LIMIT:.0f}"
    )
    if total > COST_LIMIT:
        print("the estimate is over the limit: stop")
        return None
    if not args.run and not args.replay:
        print("no model was called; pass --run to run, or --replay to run from the cache")
        return None

    kept = None
    if args.keep or args.reuse:
        try:
            kept = kept_name(args.sample, config, library_commit())
        except RuntimeError as error:
            parser.error(str(error))

    name = "e2e-smoke" if args.smoke else f"e2e-sample-{args.sample}"
    name += f"-{args.tag}" if args.tag else ""
    name += f"-first{args.limit}" if args.limit is not None else ""
    path = RESULTS_DIR / f"{name}.json"
    rows_file = RESULTS_DIR / f"{name}.rows.jsonl"
    header = {
        "arms": list(arms),
        "config": asdict(config),
        "sample": "smoke" if args.smoke else args.sample,
        "limit": args.limit,
        "kept": kept,
        "reuse": args.reuse,
    }
    if args.background is not None:
        header["background"] = args.background
    earlier: list[dict[str, Any]] = []
    if args.resume:
        if not rows_file.exists():
            parser.error(f"--resume found no rows file {rows_file}")
        found_header, earlier = read_rows(rows_file)
        if found_header != header:
            parser.error(f"the rows file {rows_file} is of another run: {found_header}")
    elif rows_file.exists():
        parser.error(f"the rows file {rows_file} exists: pass --resume, or delete it")
    finished = {row["id"] for row in earlier}
    left = [question for question in questions if question.id not in finished]
    if args.resume:
        print(f"resuming: {len(finished)} questions finished, {len(left)} left")

    if kept is not None:
        names = [f"{kept}-{question.id}" for question in questions]
        found = existing_databases(args.url, names)
        if args.reuse:
            missing = sorted(set(names) - set(found))
            if missing:
                parser.error(
                    f"{len(missing)} kept databases do not exist, for example {missing[0]}"
                )
        else:
            done_names = {f"{kept}-{question_id}" for question_id in finished}
            partial = [found_name for found_name in found if found_name not in done_names]
            if partial and not args.resume:
                parser.error(f"{len(found)} kept databases exist already, for example {found[0]}")
            if partial:
                _delete_databases(args.url, partial)
                print(f"deleted {len(partial)} kept databases of questions that did not finish")
        print(f"{'keeping' if args.keep else 'reusing'} the memory as {kept}-<question id>")

    if args.background is not None and not existing_databases(args.url, [args.background]):
        parser.error(f"the local database {args.background} does not exist")

    commit = _commit()
    started = datetime.now(UTC)
    run_id = f"{started:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    clock_start = time.perf_counter()
    RESULTS_DIR.mkdir(exist_ok=True)
    if not args.resume:
        rows_file.write_text(json.dumps({"header": header}) + "\n")
    shards = shards_of(left, args.workers, run_id) if left else []
    job = Job(
        args.url,
        arms,
        config,
        args.replay,
        len(left),
        kept,
        args.reuse,
        args.smoke,
        rows_file,
        args.background,
    )
    if len(shards) <= 1:
        done = [run_shard(job, shard) for shard in shards]
    else:
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(len(shards), mp_context=context) as pool:
            done = list(pool.map(run_shard, [job] * len(shards), shards))
    spent = sum(found.spent for found in done)
    failed = [failure for found in done for failure in found.failed]
    if failed:
        print(f"{len(failed)} questions failed; spent ${spent:.2f}")
        for failure in failed:
            print(f"  {failure}")
        print(f"the finished rows are in {rows_file}; run the command again with --resume")
        return None
    by_id = {row["id"]: row for row in earlier}
    by_id |= {row["id"]: row for found in done for row in found.rows}
    rows = [by_id[question.id] for question in questions]
    calls = sum(found.calls for found in done)
    misses = sum(found.embedder_misses for found in done)

    summaries = {arm: summarize(rows, arm) for arm in arms}
    result_file = {
        "config": {
            "benchmark": "LongMemEval-S",
            "data_file": DATA_FILE,
            "data_revision": DATA_REVISION,
            "data_sha256": DATA_SHA256,
            "sample": (
                {
                    "name": "smoke",
                    "file": SMOKE.file,
                    "per_type": SMOKE_PER_TYPE,
                    "seed": SMOKE.seed,
                    "from": SAMPLES["a"].file,
                }
                if args.smoke
                else {"name": ALL, "questions": len(all_questions), "order": "data file"}
                if args.sample == ALL
                else {
                    "name": args.sample,
                    "file": SAMPLES[args.sample].file,
                    "per_type": PER_TYPE,
                    "seed": SAMPLES[args.sample].seed,
                    "excludes": list(SAMPLES[args.sample].excludes),
                }
            ),
            "reader": {
                "model": READER,
                "temperature": 0,
                "max_tokens": READER_MAX_TOKENS,
                "prompt": "reference, chain of thought, JSON history",
            },
            "judge": {
                "model": JUDGE,
                "temperature": 0,
                "max_tokens": JUDGE_MAX_TOKENS,
                "prompt": "reference, by question type",
            },
            "extractor": {
                "variant": config.extractor,
                "model": config.variant.model,
                "max_tokens": EXTRACTOR_MAX_TOKENS,
            },
            "embedder": {"model": embed.MODEL, "dimensions": embed.DIMENSIONS},
            "driver": "memory" if args.smoke else "hotdata",
            "location": "in-process" if args.smoke else "local",
            "retrieval": {"ranking": config.retrieval_ranking, "sessions": RETRIEVED},
            "memory": {
                "ranking": config.memory_ranking,
                "k": config.k,
                "budget": config.budget,
                "extractor": config.extractor,
                "fact_sources": config.fact_sources,
                "session_episodes": config.session_episodes,
                "episodes": config.episodes,
                "chunk_chars": config.chunk_chars,
                "kept": kept,
                "reused": args.reuse,
                "background": (
                    None
                    if args.background is None
                    else {"database": args.background, "namespace": "/".join(BACKGROUND)}
                ),
            },
            "arms": {
                "full": "every session of the question",
                "retrieval": (
                    f"the top {RETRIEVED} sessions of the {config.retrieval_ranking} search "
                    "over episodes"
                ),
                "memory": (
                    f"capture on each session in date order with the {config.extractor} "
                    + (
                        f"extractor, in chunks of {config.chunk_chars} characters, then recall"
                        if config.chunk_chars is not None
                        else "extractor, then recall"
                    )
                    + f" with the {config.memory_ranking} ranking, k "
                    f"{config.k}, budget {config.budget} characters, episodes "
                    f"{config.episodes}, as_of the end of the question day"
                ),
            },
            "questions": len(questions),
            "estimate_usd": round(total, 2),
            "replay": args.replay,
            "workers": len(shards),
            "spent_usd": round(spent, 2),
            "resumed_questions": len(finished),
            "model_calls": calls,
            "sessions_at_limit": None
            if args.reuse
            else sum(
                row["arms"]["memory"]["sessions_at_limit"]
                for row in rows
                if "memory" in row["arms"]
            ),
            "embedder_misses": misses,
            "hotmemory_commit": commit,
            "run_id": run_id,
            "started_at": started.isoformat(),
            "duration_s": round(time.perf_counter() - clock_start, 1),
        },
        "summary": summaries,
        "questions": rows,
    }
    path.write_text(json.dumps(result_file, indent=1) + "\n")
    rows_file.unlink()
    for arm in arms:
        found = summaries[arm]["all"]
        print(f"{arm}: {found['correct']}/{found['questions']} = {found['accuracy']:.3f}")
    print(f"spent ${spent:.2f}; wrote {path}")
    return path


if __name__ == "__main__":
    main()
