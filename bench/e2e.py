"""The end-to-end score: three arms on the sample, with one reader and one judge.

Run it from the root of the repository. Without `--run`, it prints the cost estimate and
stops. With `--replay`, it calls no model, and it stops at the first reply that the cache
does not hold:

    uv run --group bench --env-file .env python -m bench.e2e
    uv run --group bench --env-file .env python -m bench.e2e --run
    uv run --group bench --env-file .env python -m bench.e2e --replay --arms memory

It writes one JSON file into `bench/results/` with the configuration and the results.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import random
import time
import uuid
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench import embed, prompts
from bench.data import (
    DATA_DIR,
    DATA_FILE,
    DATA_REVISION,
    DATA_SHA256,
    QUESTION_TYPES,
    Question,
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
    Reply,
)
from bench.retrieval import RANKINGS, RESULTS_DIR, _commit
from hotmemory import Embedder, Fact, Filter, Memory, Record, Store

HERE = Path(__file__).resolve().parent
PER_TYPE = 9
ARMS = ("full", "retrieval", "memory")
RETRIEVED = 10
RECALL_K = 30
RECALL_BUDGET = 8000
COST_LIMIT = 50.0
EXTRACT_OVERHEAD = 700
ACTOR = "longmemeval-extractor"
Models = Callable[..., Reply]


@dataclass(frozen=True)
class Config:
    """The configuration of the retrieval arm and the memory arm.

    `retrieval_ranking` ranks the sessions of the retrieval arm. The memory arm captures
    and recalls with `memory_ranking`, recalls `k` records into a block of at most
    `budget` characters, and extracts with the variant `extractor` of `prompts.VARIANTS`.
    """

    retrieval_ranking: str = "fused"
    memory_ranking: str = "fused"
    k: int = RECALL_K
    budget: int = RECALL_BUDGET
    extractor: str = "baseline"

    def __post_init__(self) -> None:
        for ranking in (self.retrieval_ranking, self.memory_ranking):
            if ranking not in RANKINGS:
                raise ValueError(f"ranking must be one of {', '.join(RANKINGS)}, got {ranking!r}")
        if self.extractor not in prompts.VARIANTS:
            raise ValueError(f"no extractor variant {self.extractor!r}")
        if self.k < 1 or self.budget < 1:
            raise ValueError("k and budget must be at least 1")

    @property
    def variant(self) -> prompts.Variant:
        """Return the extractor variant."""
        return prompts.VARIANTS[self.extractor]


BASELINE = Config()
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


def extractor(
    models: Models, variant: prompts.Variant = prompts.VARIANTS["baseline"]
) -> Callable[[str, datetime | None, list[Record]], list[Fact]]:
    """Return the extractor callable of `capture`: one call to the variant's model per session."""

    def extract(text: str, observed_at: datetime | None, current: list[Record]) -> list[Fact]:
        date = observed_at.strftime("%Y/%m/%d (%a) %H:%M") if observed_at else "unknown"
        known = [f"- {record.content}" for record in current]
        prompt = prompts.extract(text, date, known, variant.prompt)
        reply = models(variant.model, prompt, EXTRACTOR_MAX_TOKENS, prompts.EXTRACT_SCHEMA)
        facts = json.loads(reply.text)["facts"]
        return [
            Fact(kind="fact", subject=item["subject"].strip(), content=item["content"].strip())
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
    extracted: int = 0

    @property
    def correct(self) -> bool:
        return "yes" in self.verdict.text.lower()


def answer(
    models: Models, question: Question, prompt: str, context_chars: int, extracted: int = 0
) -> Arm:
    """Read `prompt` with READER, judge the answer with JUDGE, and return the arm."""
    reply = models(READER, prompt, READER_MAX_TOKENS)
    verdict = models(JUDGE, prompts.judge(question, reply.text), JUDGE_MAX_TOKENS)
    return Arm(context_chars, reply, verdict, extracted)


def run_question(
    question: Question,
    models: Models,
    stores: Mapping[str, Store],
    empty: Callable[[Sequence[str]], None],
    run_id: str,
    arms: Sequence[str] = ARMS,
    config: Config = BASELINE,
) -> dict[str, Arm]:
    """Run each arm on `question`, and empty its namespaces after.

    `stores` maps each ranking to a store, and every store holds the same records.
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
        extract = extractor(models, config.variant)
        written = 0
        for session in question.sessions:
            written += len(
                memory.capture(
                    prompts.session_text(session),
                    namespace,
                    extract,
                    actor=ACTOR,
                    observed_at=session.date,
                )
            )
        _, block = memory.recall(
            question.question, [namespace], as_of=question.as_of, budget=config.budget, k=config.k
        )
        empty(namespace)
        prompt = prompts.read_facts(question, block)
        result["memory"] = answer(models, question, prompt, len(block), written)
    return result


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


@contextmanager
def local_store(
    url: str, run_id: str, embedder: Embedder | None = None
) -> Iterator[tuple[dict[str, Store], Callable[[Sequence[str]], None]]]:
    """Yield a HotdataStore for each ranking, and a call that empties a namespace.

    The stores share one new local database, and the database is deleted after. Without
    `embedder`, the stores embed with `embed.openai_embedder()`.
    """
    import pyarrow as pa
    from hotdata_framework import HotdataClient

    from hotmemory.hotdata import CUE_TABLE, RECORD_TABLES, HotdataStore

    client = HotdataClient("local", "local", host=url)
    embedder = embedder or embed.openai_embedder()
    store = HotdataStore.provision(
        f"hotmemory-bench-{run_id}",
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
        name: HotdataStore(
            client,
            store.database,
            embedder=embedder,
            model=embed.MODEL,
            dimensions=embed.DIMENSIONS,
            ranking=name,  # type: ignore[arg-type]
        )
        for name in RANKINGS
    }
    try:
        yield stores, empty
    finally:
        client.delete_managed_database(store.database)
        client.close()


@dataclass(frozen=True)
class Job:
    """What every worker of one run shares."""

    url: str
    arms: tuple[str, ...]
    config: Config
    replay: bool
    total: int


@dataclass(frozen=True)
class Shard:
    """The questions of one worker, and the run id that names its database."""

    questions: list[Question]
    run_id: str


@dataclass(frozen=True)
class Done:
    """The rows of one shard, and what its model calls cost."""

    rows: list[dict[str, Any]]
    spent: float
    calls: int
    embedder_misses: int


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
                "reader_input_tokens": found.answer.input_tokens,
                "answer": found.answer.text,
                "verdict": found.verdict.text,
            }
            for arm, found in result.items()
        },
    }


def run_shard(job: Job, shard: Shard) -> Done:
    """Run the questions of `shard` in order, over a new local database of its own.

    The worker opens its own model cache, embedder, and store, so it can run in its own
    process. It prints one line for each question.
    """
    from bench.models import Offline, Remote

    models = CachedModels(Offline() if job.replay else Remote(), CACHE_DIR / "replies.sqlite")
    embedder = embed.openai_embedder()
    rows: list[dict[str, Any]] = []
    try:
        with local_store(job.url, shard.run_id, embedder) as (stores, empty):
            for question in shard.questions:
                result = run_question(
                    question, models, stores, empty, shard.run_id, job.arms, job.config
                )
                rows.append(row_of(question, result))
                marks = " ".join(
                    f"{arm}={'yes' if found.correct else 'no'}" for arm, found in result.items()
                )
                print(
                    f"{shard.run_id} {len(rows)}/{len(shard.questions)} of {job.total} "
                    f"{question.id} {marks} spent ${models.spent:.2f}",
                    flush=True,
                )
    finally:
        models.close()
        embedder.close()
    return Done(rows, models.spent, models.calls, embedder.misses)


def main(argv: Sequence[str] | None = None) -> Path | None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run", action="store_true", help="call the models; without it, only estimate"
    )
    parser.add_argument("--arms", default=",".join(ARMS), help="a comma-separated list of arms")
    parser.add_argument(
        "--limit", type=int, default=None, help="the first N questions of the sample"
    )
    parser.add_argument("--sample", choices=tuple(SAMPLES), default="a", help="the sample to run")
    parser.add_argument("--retrieval-ranking", choices=RANKINGS, default="fused")
    parser.add_argument("--memory-ranking", choices=RANKINGS, default="fused")
    parser.add_argument("--recall-k", type=int, default=RECALL_K, help="k of the memory recall")
    parser.add_argument(
        "--recall-budget", type=int, default=RECALL_BUDGET, help="characters of the memory block"
    )
    parser.add_argument("--extractor", choices=tuple(prompts.VARIANTS), default="baseline")
    parser.add_argument(
        "--replay", action="store_true", help="call no model: use cached replies, stop at a miss"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="processes that run the questions, each over a database of its own",
    )
    parser.add_argument("--tag", default="", help="a suffix for the name of the result file")
    parser.add_argument("--url", default="http://localhost:3000")
    args = parser.parse_args(argv)
    arms = tuple(arm for arm in args.arms.split(",") if arm)
    if not set(arms) <= set(ARMS):
        parser.error(f"arms must be among {', '.join(ARMS)}")
    if args.workers < 1:
        parser.error("workers must be at least 1")
    try:
        config = Config(
            retrieval_ranking=args.retrieval_ranking,
            memory_ranking=args.memory_ranking,
            k=args.recall_k,
            budget=args.recall_budget,
            extractor=args.extractor,
        )
    except ValueError as error:
        parser.error(str(error))

    all_questions = {question.id: question for question in load(DATA_DIR / DATA_FILE)}
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

    commit = _commit()
    started = datetime.now(UTC)
    run_id = f"{started:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    clock_start = time.perf_counter()
    shards = shards_of(questions, args.workers, run_id)
    job = Job(args.url, arms, config, args.replay, len(questions))
    if len(shards) == 1:
        done = [run_shard(job, shards[0])]
    else:
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(len(shards), mp_context=context) as pool:
            done = list(pool.map(run_shard, [job] * len(shards), shards))
    by_id = {row["id"]: row for found in done for row in found.rows}
    rows = [by_id[question.id] for question in questions]
    spent = sum(found.spent for found in done)
    calls = sum(found.calls for found in done)
    misses = sum(found.embedder_misses for found in done)

    summaries = {arm: summarize(rows, arm) for arm in arms}
    result_file = {
        "config": {
            "benchmark": "LongMemEval-S",
            "data_file": DATA_FILE,
            "data_revision": DATA_REVISION,
            "data_sha256": DATA_SHA256,
            "sample": {
                "name": args.sample,
                "file": SAMPLES[args.sample].file,
                "per_type": PER_TYPE,
                "seed": SAMPLES[args.sample].seed,
                "excludes": list(SAMPLES[args.sample].excludes),
            },
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
            "driver": "hotdata",
            "location": "local",
            "retrieval": {"ranking": config.retrieval_ranking, "sessions": RETRIEVED},
            "memory": {
                "ranking": config.memory_ranking,
                "k": config.k,
                "budget": config.budget,
                "extractor": config.extractor,
            },
            "arms": {
                "full": "every session of the question",
                "retrieval": (
                    f"the top {RETRIEVED} sessions of the {config.retrieval_ranking} search "
                    "over episodes"
                ),
                "memory": (
                    f"capture on each session in date order with the {config.extractor} "
                    f"extractor, then recall with the {config.memory_ranking} ranking, k "
                    f"{config.k}, budget {config.budget} characters, as_of the end of the "
                    "question day"
                ),
            },
            "questions": len(questions),
            "estimate_usd": round(total, 2),
            "replay": args.replay,
            "workers": len(shards),
            "spent_usd": round(spent, 2),
            "model_calls": calls,
            "embedder_misses": misses,
            "hotmemory_commit": commit,
            "run_id": run_id,
            "started_at": started.isoformat(),
            "duration_s": round(time.perf_counter() - clock_start, 1),
        },
        "summary": summaries,
        "questions": rows,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    name = f"e2e-sample-{args.sample}" + (f"-{args.tag}" if args.tag else "")
    name += f"-first{args.limit}" if args.limit is not None else ""
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(json.dumps(result_file, indent=1) + "\n")
    for arm in arms:
        found = summaries[arm]["all"]
        print(f"{arm}: {found['correct']}/{found['questions']} = {found['accuracy']:.3f}")
    print(f"spent ${spent:.2f}; wrote {path}")
    return path


if __name__ == "__main__":
    main()
