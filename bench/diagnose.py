"""The diagnosis of the memory arm: was the answer extracted, and where did recall rank it?

For each question that the memory arm missed in a result file, this runs `capture` again
with the cached extractor replies, and keeps every fact. The replay calls no model, and it
stops at the first reply that the cache does not hold. It uses the ranking, `k`, `budget`,
and extractor variant that the result file records. A model marks the facts that hold the
answer, and the search ranks every fact for the question. If a marked fact is in the block,
a second model call tells whether the block holds every needed fact ("all present") or only
some of them ("some present").

    uv run --group bench --env-file .env python -m bench.diagnose --result <file>
"""

from __future__ import annotations

import argparse
import json
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench import prompts
from bench.data import DATA_DIR, DATA_FILE, QUESTION_TYPES, load
from bench.e2e import ACTOR, BASELINE, RECALL_BUDGET, RECALL_K, Config, extractor, local_store
from bench.embed import CACHE_DIR
from bench.models import JUDGE, CachedModels, Offline, Remote
from bench.retrieval import RANKINGS, RESULTS_DIR, _commit
from hotmemory import Filter, Memory, Record, Store
from hotmemory.contract import RECALLED_KINDS, fit_lines, is_valid_at, render_line

NOT_EXTRACTED = "not extracted"
BELOW_K = "extracted, ranked below k"
CUT = "in the top k, cut by as_of or the budget"
RECALLED = "recalled, reader missed"
CAUSES = (NOT_EXTRACTED, BELOW_K, CUT, RECALLED)
MARK = """A question about a user, its correct answer, and a numbered list of facts that a \
memory holds about the user follow. Return the numbers of the facts that state the correct \
answer itself, or a value that the answer is computed from. A fact on the same topic that \
does not state the answer or such a value does not count. Return an empty list if no fact \
counts.

Question: {question}

Correct answer: {answer}

Facts:
{facts}"""
MARK_SCHEMA = {
    "type": "object",
    "properties": {"facts": {"type": "array", "items": {"type": "integer"}}},
    "required": ["facts"],
    "additionalProperties": False,
}
COMPLETE = """A question about a user, its correct answer, and a list of facts follow. Tell \
whether the facts together state every value that the correct answer needs, so that a \
reader can give the correct answer from these facts alone. Return true if they do, and \
false if a needed value is missing.

Question: {question}

Correct answer: {answer}

Facts:
{facts}"""
COMPLETE_SCHEMA = {
    "type": "object",
    "properties": {"complete": {"type": "boolean"}},
    "required": ["complete"],
    "additionalProperties": False,
}


def block_ids(
    ranked: Sequence[Record], as_of: datetime, k: int = RECALL_K, budget: int = RECALL_BUDGET
) -> set[str]:
    """Return the ids of the records that `Memory.recall` puts in its block.

    The order is that of `recall`: the first `k` records of the ranking, then the ones
    valid at `as_of`, then the lines that fit in `budget` characters.
    """
    valid = [record for record in ranked[:k] if is_valid_at(record, as_of)]
    lines = [render_line(record) for record in valid]
    return {record.id for record in valid[: fit_lines(lines, budget)]}


def verdict(holding: set[str], best_rank: int | None, block: set[str], k: int = RECALL_K) -> str:
    """Return the cause of a miss from the facts that hold the answer and the block.

    `best_rank` is the best rank of those facts in the full ranking, before any filter.
    """
    if not holding:
        return NOT_EXTRACTED
    if holding & block:
        return RECALLED
    if best_rank is not None and best_rank <= k:
        return CUT
    return BELOW_K


def memory_config(result: dict[str, Any]) -> Config:
    """Return the configuration of the memory arm that a result file records.

    A result file of phase 5 records no `memory` section, and its run used `BASELINE`.
    """
    found = result["config"].get("memory")
    if found is None:
        return BASELINE
    return Config(
        memory_ranking=found["ranking"],
        k=found["k"],
        budget=found["budget"],
        extractor=found["extractor"],
    )


def table(rows: Sequence[dict[str, Any]]) -> str:
    """Return the groups of misses for each question type, and in total, as Markdown."""
    columns = ("missed", *CAUSES[:3], "recalled, all present", "recalled, some present")
    header = "| Type | " + " | ".join(columns) + " |"
    lines = [header, "|" + "---|" * (len(columns) + 1)]
    kinds = [kind for kind in QUESTION_TYPES if any(row["type"] == kind for row in rows)]
    for kind in [*kinds, "all"]:
        chosen = [row for row in rows if kind in ("all", row["type"])]
        groups = Counter(group(row) for row in chosen)
        counts = [len(chosen), *(groups[name] for name in columns[1:])]
        lines.append(f"| {kind} | " + " | ".join(str(count) for count in counts) + " |")
    return "\n".join(lines)


def group(row: dict[str, Any]) -> str:
    """Return the group of one diagnosed miss, with the recalled group split in two."""
    if row["cause"] != RECALLED:
        return str(row["cause"])
    return f"recalled, {row['present']} present"


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--result", type=Path, default=RESULTS_DIR / "e2e-sample-a.json")
    parser.add_argument("--url", default="http://localhost:3000")
    args = parser.parse_args(argv)
    commit = _commit()

    result = json.loads(args.result.read_text())
    config = memory_config(result)
    missed = [row["id"] for row in result["questions"] if not row["arms"]["memory"]["correct"]]
    questions = {question.id: question for question in load(DATA_DIR / DATA_FILE)}
    replay = CachedModels(Offline(), CACHE_DIR / "replies.sqlite")
    marker = CachedModels(Remote(), CACHE_DIR / "replies.sqlite")
    rows: list[dict[str, Any]] = []
    run_id = f"diagnose-{datetime.now(UTC):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"
    with local_store(args.url, run_id) as (stores, empty):
        memory = Memory(stores[config.memory_ranking])
        extract = extractor(replay, config.variant)
        for count, question_id in enumerate(missed, start=1):
            question = questions[question_id]
            namespace = ("bench", "lme_s", run_id, question.id)
            for session in question.sessions:
                memory.capture(
                    prompts.session_text(session),
                    namespace,
                    extract,
                    actor=ACTOR,
                    observed_at=session.date,
                )
            store = stores[config.memory_ranking]
            facts = store.list(namespace, Filter(kind=RECALLED_KINDS), limit=10_000)
            marked = _mark(marker, question.question, question.answer, facts)
            holding = {facts[n - 1].id for n in marked}
            ranks: dict[str, int | None] = {}
            for name in RANKINGS:
                ranked = _search(stores[name], question.question, namespace, len(facts))
                found = [n for n, r in enumerate(ranked, start=1) if r.id in holding]
                ranks[name] = found[0] if found else None
            top = _search(store, question.question, namespace, config.k)
            block = block_ids(top, question.as_of, config.k, config.budget)
            cause = verdict(holding, ranks[config.memory_ranking], block, config.k)
            present = None
            if cause == RECALLED:
                shown = [fact.content for fact in facts if fact.id in holding & block]
                whole = _complete(marker, question.question, question.answer, shown)
                present = "all" if whole else "some"
            rows.append(
                {
                    "id": question.id,
                    "type": question.type,
                    "facts": len(facts),
                    "holding": [facts[n - 1].content for n in marked],
                    "best_rank": ranks,
                    "cause": cause,
                    "present": present,
                }
            )
            empty(namespace)
            print(f"{count}/{len(missed)} {question.id} {group(rows[-1])} {ranks}", flush=True)
    replay.close()
    marker.close()
    causes = Counter(row["cause"] for row in rows)
    output = {
        "config": {
            "source": str(args.result.resolve().relative_to(Path.cwd())),
            "memory": {
                "ranking": config.memory_ranking,
                "k": config.k,
                "budget": config.budget,
                "extractor": config.extractor,
            },
            "marker": JUDGE,
            "hotmemory_commit": commit,
            "spent_usd": round(marker.spent, 4),
            "model_calls": marker.calls,
        },
        "causes": {cause: causes[cause] for cause in CAUSES},
        "groups": dict(Counter(group(row) for row in rows).most_common()),
        "questions": rows,
    }
    path: Path = args.result.with_name(args.result.stem + "-diagnosis.json")
    path.write_text(json.dumps(output, indent=1) + "\n")
    print(table(rows))
    print(f"spent ${marker.spent:.4f}; wrote {path}")
    return path


def _mark(marker: CachedModels, question: str, answer: str, facts: Sequence[Record]) -> list[int]:
    """Return the numbers, from 1, of the facts that the marker says hold the answer."""
    numbered = "\n".join(f"{n}. {fact.content}" for n, fact in enumerate(facts, 1))
    prompt = MARK.format(question=question, answer=answer, facts=numbered)
    reply = marker(JUDGE, prompt, 1000, MARK_SCHEMA)
    return sorted({n for n in json.loads(reply.text)["facts"] if 1 <= n <= len(facts)})


def _complete(marker: CachedModels, question: str, answer: str, facts: Sequence[str]) -> bool:
    """Return whether the marker says that `facts` hold every value that the answer needs."""
    listed = "\n".join(f"- {fact}" for fact in facts)
    prompt = COMPLETE.format(question=question, answer=answer, facts=listed)
    reply = marker(JUDGE, prompt, 100, COMPLETE_SCHEMA)
    return bool(json.loads(reply.text)["complete"])


def _search(store: Store, query: str, namespace: Sequence[str], k: int) -> list[Record]:
    """Return the first `k` facts under `namespace`, ranked for `query` by `store`."""
    hits = store.search(query, [namespace], Filter(kind=RECALLED_KINDS), k=k)
    return [hit.record for hit in hits]


if __name__ == "__main__":
    main()
