"""The diagnosis of the memory arm: was the answer extracted, and where did recall rank it?

For each question that the memory arm missed in a result file, this runs `capture` again
with the cached extractor replies, and keeps every fact. A model marks the facts that hold
the answer, and the search ranks every fact for the question.

    uv run --group bench --env-file .env python -m bench.diagnose
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from bench import prompts
from bench.data import DATA_DIR, DATA_FILE, load
from bench.e2e import ACTOR, RECALL_BUDGET, RECALL_K, extractor, local_store
from bench.embed import CACHE_DIR
from bench.models import JUDGE, CachedModels, Remote
from bench.retrieval import RESULTS_DIR, _commit
from hotmemory import Filter, Memory, Record
from hotmemory.contract import RECALLED_KINDS, fit_lines, is_valid_at, render_line

SPEND_LIMIT = 1.0
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


def verdict(holding: set[str], best_rank: int | None, block: set[str]) -> str:
    """Return the cause of a miss from the facts that hold the answer and the block.

    `best_rank` is the best rank of those facts in the full ranking, before any filter.
    """
    if not holding:
        return "not extracted"
    if holding & block:
        return "recalled, reader missed"
    if best_rank is not None and best_rank <= RECALL_K:
        return "in the top k, cut by as_of or the budget"
    return "extracted, ranked below k"


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--result", type=Path, default=RESULTS_DIR / "e2e-sample.json")
    parser.add_argument("--url", default="http://localhost:3000")
    args = parser.parse_args(argv)
    commit = _commit()

    result = json.loads(args.result.read_text())
    missed = [row["id"] for row in result["questions"] if not row["arms"]["memory"]["correct"]]
    questions = {question.id: question for question in load(DATA_DIR / DATA_FILE)}
    models = CachedModels(Remote(), CACHE_DIR / "replies.sqlite")
    rows: list[dict[str, Any]] = []
    run_id = "diagnose-" + result["config"]["run_id"]
    marked_spend = 0.0
    with local_store(args.url, run_id) as (store, empty):
        memory = Memory(store)
        extract = extractor(models)
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
                if models.spent - marked_spend > SPEND_LIMIT:
                    raise RuntimeError(f"the replay spent ${models.spent:.2f}: the cache missed")
            facts = store.list(namespace, Filter(kind=RECALLED_KINDS), limit=10_000)
            numbered = "\n".join(f"{n}. {fact.content}" for n, fact in enumerate(facts, 1))
            replayed = models.spent
            reply = models(
                JUDGE,
                MARK.format(question=question.question, answer=question.answer, facts=numbered),
                1000,
                MARK_SCHEMA,
            )
            marked_spend += models.spent - replayed
            marked = sorted({n for n in json.loads(reply.text)["facts"] if 1 <= n <= len(facts)})
            holding = {facts[n - 1].id for n in marked}
            ranks: dict[str, int | None] = {}
            block: set[str] = set()
            for name in ("fused", "vector"):
                ranked = _search(store, name, question.question, namespace, len(facts))
                found = [n for n, r in enumerate(ranked, start=1) if r.id in holding]
                ranks[name] = found[0] if found else None
                if name == "fused":
                    top = _search(store, name, question.question, namespace, RECALL_K)
                    block = block_ids(top, question.as_of)
            cause = verdict(holding, ranks["fused"], block)
            rows.append(
                {
                    "id": question.id,
                    "type": question.type,
                    "facts": len(facts),
                    "holding": [facts[n - 1].content for n in marked],
                    "best_rank": ranks,
                    "cause": cause,
                }
            )
            empty(namespace)
            print(f"{count}/{len(missed)} {question.id} {cause} {ranks}", flush=True)
    models.close()
    causes = Counter(row["cause"] for row in rows)
    output = {
        "config": {
            "source": str(args.result.relative_to(Path.cwd())),
            "recall_k": RECALL_K,
            "recall_budget": RECALL_BUDGET,
            "marker": JUDGE,
            "hotmemory_commit": commit,
            "spent_usd": round(models.spent, 4),
            "model_calls": models.calls,
        },
        "causes": dict(causes.most_common()),
        "questions": rows,
    }
    path = RESULTS_DIR / "e2e-sample-diagnosis.json"
    path.write_text(json.dumps(output, indent=1) + "\n")
    print(json.dumps(output["causes"]), f"spent ${models.spent:.4f}; wrote {path}")
    return path


def _search(store: Any, ranking: str, query: str, namespace: Sequence[str], k: int) -> list[Any]:
    """Return every fact under `namespace`, ranked for `query` by `ranking`."""
    from hotmemory.hotdata import HotdataStore

    ranked = HotdataStore(
        store.client,
        store.database,
        embedder=store._embedder,
        model=store._model,
        dimensions=store._dimensions,
        ranking=ranking,  # type: ignore[arg-type]
    )
    return [
        hit.record for hit in ranked.search(query, [namespace], Filter(kind=RECALLED_KINDS), k=k)
    ]


if __name__ == "__main__":
    main()
