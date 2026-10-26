"""The BEAM answer prompt and score, as the official code at commit b2da22e gives them.

The prompts in `bench/beam_prompts/` are copied from github.com/mohammadtavakoli78/BEAM at
commit b2da22e, under the MIT license in `bench/beam_prompts/LICENSE`. The functions here
follow `src/evaluation/compute_metrics.py` and `src/evaluation/report_results.py` there:

- Each nugget of the rubric gets one judge call with the unified judge prompt, which sees
  the nugget and the response but not the question. The reply is parsed as JSON, and its
  `score` is 0, 0.5, or 1. The nugget score of a question is the mean.
- Event ordering also splits the response on newlines, matches each line to a nugget with
  one binary call for each unmatched nugget, and computes Kendall tau-b over the matched
  lists. The report scores event ordering by `tau_norm`, (tau-b + 1) / 2, alone.
- The aggregate is the mean over the 10 abilities.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from json_repair import repair_json
from scipy.stats import kendalltau

from bench.beam import ABILITIES, Probe
from bench.models import BEAM_MINI, Reply

BEAM_COMMIT = "b2da22e"
PROMPTS = Path(__file__).resolve().parent / "beam_prompts"
ANSWER_PROMPT = (PROMPTS / "answer.txt").read_text()
JUDGE_PROMPT = (PROMPTS / "judge.txt").read_text()
MATCH_SYSTEM = (PROMPTS / "match_system.txt").read_text()
MATCH_USER = (PROMPTS / "match_user.txt").read_text()
JUDGE = BEAM_MINI
JUDGE_MAX_TOKENS = 1000
MATCH_MAX_TOKENS = 50
EVENT_ORDERING = "event_ordering"
Models = Callable[..., Reply]


@dataclass(frozen=True)
class Score:
    """The score of one response to one probe.

    `score` is the official score: `tau_norm` for event ordering, else `nugget`. `nugget`
    is the mean of `nuggets`, the judge score of each nugget. For event ordering, `f1` is
    the F1 of the line match, and `tau_undefined` is True if Kendall tau-b was not defined,
    which the official code leaves as NaN and this score counts as 0.
    """

    score: float
    nugget: float
    nuggets: tuple[float, ...]
    tau_norm: float | None = None
    f1: float | None = None
    tau_undefined: bool = False


def answer_prompt(context: str, question: str) -> str:
    """Return the official RAG answer prompt for `question` over `context`."""
    return ANSWER_PROMPT.replace("<context>", context).replace("<question>", question)


def judge_prompt(item: str, response: str) -> str:
    """Return the official judge prompt for one rubric item and one response."""
    return JUDGE_PROMPT.replace("<rubric_item>", item).replace("<llm_response>", response)


def parse_json_response(response: str) -> Any:
    """Return the JSON in `response`, as the official `parse_json_response` finds it.

    Raises ValueError if no JSON is found.
    """
    response = response.strip()
    if response.startswith("```"):
        match = re.search(r"```(?:json)?\s*(\[.*\]|\{.*\})\s*```", response, re.DOTALL)
        if match:
            response = match.group(1).strip()
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass
    match = re.search(r"(\{.*?\}|\[.*?\])", response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception as error:
            raise ValueError(f"Found possible JSON but failed to parse it: {error}") from error
    raise ValueError("No valid JSON found in response.")


def judge_reply(text: str) -> float:
    """Return the score of one judge reply, with the official parse and its repair fallback."""
    try:
        parsed = parse_json_response(text.strip())
    except ValueError:
        parsed = json.loads(repair_json(text.strip()))
    return float(parsed["score"])


def nugget_scores(models: Models, rubric: Sequence[str], response: str) -> tuple[float, ...]:
    """Return the judge score of each rubric item for `response`, one call each."""
    return tuple(
        judge_reply(models(JUDGE, judge_prompt(item, response), JUDGE_MAX_TOKENS).text)
        for item in rubric
    )


def same_event(models: Models, first: str, second: str) -> bool:
    """Return True if the binary classifier says the two snippets name the same event."""
    prompt = MATCH_USER.replace("{first}", first, 1).replace("{second}", second, 1)
    reply = models(JUDGE, prompt, MATCH_MAX_TOKENS, None, system=MATCH_SYSTEM)
    return "yes" in reply.text.lower()


def align(
    models: Models, reference: Sequence[str], system: Sequence[str]
) -> tuple[list[str], list[str]]:
    """Return `reference` and `system` with each line replaced by its first unused match.

    Each line of `system` is compared with each reference item that no earlier line
    matched, in order, until one matches. A line with no match stays as it is.
    """
    used: set[int] = set()
    out = []
    for line in system:
        matched = None
        for index, item in enumerate(reference):
            if index not in used and same_event(models, item, line):
                matched = index
                break
        if matched is None:
            out.append(line)
        else:
            out.append(reference[matched])
            used.add(matched)
    return list(reference), out


def order_score(reference: Sequence[str], system: Sequence[str]) -> dict[str, float]:
    """Return the precision, recall, F1, `tau_norm`, and final score of two aligned lists.

    `tau_norm` is NaN if Kendall tau-b is not defined, as in the official code.
    """
    tp = len(set(reference) & set(system))
    fp = len([x for x in system if x not in reference])
    fn = len([x for x in reference if x not in system])
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    union = list(dict.fromkeys([*reference, *system]))
    tie_rank = len(union) + 1

    def to_rank(seq: Sequence[str]) -> list[int]:
        ranks = {item: i + 1 for i, item in enumerate(seq)}
        return [ranks.get(item, tie_rank) for item in union]

    tau_b = kendalltau(to_rank(reference), to_rank(system), variant="b", method="auto")[0]
    tau_norm = (float(tau_b) + 1) / 2
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tau_norm": tau_norm,
        "final_score": tau_norm * f1,
    }


def score(models: Models, probe: Probe, response: str) -> Score:
    """Return the official score of `response` to `probe`."""
    nuggets = nugget_scores(models, probe.rubric, response)
    nugget = sum(nuggets) / len(nuggets)
    if probe.ability != EVENT_ORDERING:
        return Score(nugget, nugget, nuggets)
    reference, system = align(models, probe.rubric, response.split("\n"))
    order = order_score(reference, system)
    undefined = math.isnan(order["tau_norm"])
    tau_norm = 0.0 if undefined else order["tau_norm"]
    return Score(tau_norm, nugget, nuggets, tau_norm, order["f1"], undefined)


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return the scores of `rows`, each with `ability`, `domain`, and `score`.

    `mean` is the mean over the abilities that `rows` hold, the aggregate of the official
    report. `questions` is the mean over every row.
    """
    by_ability = {
        ability: statistics.mean(r["score"] for r in rows if r["ability"] == ability)
        for ability in ABILITIES
        if any(r["ability"] == ability for r in rows)
    }
    domains = sorted({r["domain"] for r in rows})
    by_domain = {d: statistics.mean(r["score"] for r in rows if r["domain"] == d) for d in domains}
    return {
        "mean": statistics.mean(by_ability.values()) if by_ability else 0.0,
        "questions": statistics.mean(r["score"] for r in rows) if rows else 0.0,
        "count": len(rows),
        "by_ability": by_ability,
        "by_domain": by_domain,
    }
