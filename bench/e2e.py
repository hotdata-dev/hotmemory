"""The end-to-end score: three arms on the sample, with one reader and one judge.

Run it from the root of the repository. Without `--run`, it prints the cost estimate and
stops:

    uv run --group bench --env-file .env python -m bench.e2e
    uv run --group bench --env-file .env python -m bench.e2e --run

It writes one JSON file into `bench/results/` with the configuration and the results.
"""

from __future__ import annotations

import argparse
import json
import random
import time
import uuid
from collections.abc import Callable, Collection, Iterator, Sequence
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
    EXTRACTOR,
    EXTRACTOR_MAX_TOKENS,
    JUDGE,
    JUDGE_MAX_TOKENS,
    PRICES,
    READER,
    READER_MAX_TOKENS,
    CachedModels,
    Reply,
)
from bench.retrieval import RESULTS_DIR, _commit
from hotmemory import Fact, Filter, Memory, Record, Store

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


def extractor(models: Models) -> Callable[[str, datetime | None, list[Record]], list[Fact]]:
    """Return the extractor callable of `capture`: one call to EXTRACTOR for each session."""

    def extract(text: str, observed_at: datetime | None, current: list[Record]) -> list[Fact]:
        date = observed_at.strftime("%Y/%m/%d (%a) %H:%M") if observed_at else "unknown"
        known = [f"- {record.content}" for record in current]
        prompt = prompts.extract(text, date, known)
        reply = models(EXTRACTOR, prompt, EXTRACTOR_MAX_TOKENS, prompts.EXTRACT_SCHEMA)
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
    store: Store,
    empty: Callable[[Sequence[str]], None],
    run_id: str,
    arms: Sequence[str] = ARMS,
) -> dict[str, Arm]:
    """Run each arm on `question`, and empty its namespaces after."""
    result: dict[str, Arm] = {}
    if "full" in arms:
        prompt = prompts.read_chats(question, question.sessions)
        result["full"] = answer(models, question, prompt, len(prompt))
    if "retrieval" in arms:
        namespace = ("bench", "lme_s", run_id, "retrieval", question.id)
        write_episodes(store, namespace, question)
        hits = store.search(question.question, [namespace], Filter(kind="episode"), k=RETRIEVED)
        empty(namespace)
        by_id = {session.id: session for session in question.sessions}
        chosen = [by_id[session_of(hit.record.sources)] for hit in hits]
        prompt = prompts.read_chats(question, chosen)
        result["retrieval"] = answer(models, question, prompt, len(prompt))
    if "memory" in arms:
        namespace = ("bench", "lme_s", run_id, "memory", question.id)
        memory = Memory(store)
        extract = extractor(models)
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
            question.question, [namespace], as_of=question.as_of, budget=RECALL_BUDGET, k=RECALL_K
        )
        empty(namespace)
        prompt = prompts.read_facts(question, block)
        result["memory"] = answer(models, question, prompt, len(block), written)
    return result


def estimate(
    questions: Sequence[Question], tokens: Tokens, arms: Sequence[str] = ARMS
) -> dict[str, float]:
    """Return an upper estimate of the price of each arm in US dollars, from token counts.

    The full arm counts its prompts. The retrieval arm counts the RETRIEVED longest
    sessions of each question. The memory arm counts each session with EXTRACT_OVERHEAD
    tokens of prompt and 500 tokens of output, and a reader prompt of RECALL_BUDGET
    characters. Every reader reply counts READER_MAX_TOKENS, and every judge prompt 400
    tokens.
    """
    reader_in, reader_out = PRICES[READER]
    extract_in, extract_out = PRICES[EXTRACTOR]
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
            cost["memory"] += (RECALL_BUDGET // 3 + 300) * reader_in / 1e6 + read_out + judge
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
def local_store(url: str, run_id: str) -> Iterator[tuple[Store, Callable[[Sequence[str]], None]]]:
    """Yield a fused HotdataStore over a new local database, and a call that empties a namespace."""
    import pyarrow as pa
    from hotdata_framework import HotdataClient

    from hotmemory.hotdata import CUE_TABLE, RECORD_TABLES, HotdataStore

    client = HotdataClient("local", "local", host=url)
    store = HotdataStore.provision(
        f"hotmemory-bench-{run_id}",
        embedder=embed.openai_embedder(),
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

    try:
        yield store, empty
    finally:
        client.delete_managed_database(store.database)
        client.close()


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
    parser.add_argument("--url", default="http://localhost:3000")
    args = parser.parse_args(argv)
    arms = tuple(arm for arm in args.arms.split(",") if arm)
    if not set(arms) <= set(ARMS):
        parser.error(f"arms must be among {', '.join(ARMS)}")

    all_questions = {question.id: question for question in load(DATA_DIR / DATA_FILE)}
    sample = sample_ids(args.sample, list(all_questions.values()))
    questions = [all_questions[question_id] for question_id in sample][: args.limit]

    tokens = embed.TiktokenTokens(encoding="o200k_base")
    cost = estimate(questions, tokens, arms)
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
    if not args.run:
        print("no model was called; pass --run to run")
        return None

    from bench.models import Remote

    started = datetime.now(UTC)
    run_id = f"{started:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    models = CachedModels(Remote(), CACHE_DIR / "replies.sqlite")
    rows: list[dict[str, Any]] = []
    clock_start = time.perf_counter()
    with local_store(args.url, run_id) as (store, empty):
        for count, question in enumerate(questions, start=1):
            result = run_question(question, models, store, empty, run_id, arms)
            rows.append(
                {
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
            )
            marks = " ".join(
                f"{arm}={'yes' if found.correct else 'no'}" for arm, found in result.items()
            )
            print(
                f"{count}/{len(questions)} {question.id} {marks} spent ${models.spent:.2f}",
                flush=True,
            )
    models.close()

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
            "extractor": {"model": EXTRACTOR, "max_tokens": EXTRACTOR_MAX_TOKENS},
            "embedder": {"model": embed.MODEL, "dimensions": embed.DIMENSIONS},
            "driver": "hotdata",
            "ranking": "fused",
            "location": "local",
            "arms": {
                "full": "every session of the question",
                "retrieval": f"the top {RETRIEVED} sessions of the fused search over episodes",
                "memory": (
                    "capture on each session in date order, then recall with k "
                    f"{RECALL_K}, budget {RECALL_BUDGET} characters, as_of the end of "
                    "the question day"
                ),
            },
            "questions": len(questions),
            "estimate_usd": round(total, 2),
            "spent_usd": round(models.spent, 2),
            "model_calls": models.calls,
            "hotmemory_commit": _commit(),
            "run_id": run_id,
            "started_at": started.isoformat(),
            "duration_s": round(time.perf_counter() - clock_start, 1),
        },
        "summary": summaries,
        "questions": rows,
    }
    RESULTS_DIR.mkdir(exist_ok=True)
    name = f"e2e-sample-{args.sample}" + (f"-first{args.limit}" if args.limit is not None else "")
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(json.dumps(result_file, indent=1) + "\n")
    for arm in arms:
        found = summaries[arm]["all"]
        print(f"{arm}: {found['correct']}/{found['questions']} = {found['accuracy']:.3f}")
    print(f"spent ${models.spent:.2f}; wrote {path}")
    return path


if __name__ == "__main__":
    main()
