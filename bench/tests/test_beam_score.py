import hashlib
import json
import math
import random
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from json_repair import repair_json
from scipy.stats import kendalltau

from bench import beam_score
from bench.beam import Probe
from bench.beam_score import (
    JUDGE,
    MATCH_SYSTEM,
    align,
    answer_prompt,
    judge_prompt,
    judge_reply,
    order_score,
    parse_json_response,
    score,
    summarize,
)
from bench.models import Reply

PROMPT_SHA256 = {
    "answer.txt": "ed89b98d1024432a46bd297345961cf89d2429746daec37befb50db46e0c914a",
    "judge.txt": "593373c642a288a7b590577d8a8fc92c3f9a2b70e2f64ad6e59a040a6c56b7f5",
    "match_system.txt": "0863c78c1b27d36c97bb7bfd2d4d6b88ef69b093e92bb4719e665527722413e2",
    "match_user.txt": "8686f5ce5009350f1d04fccc06fc23a234cfcd956603c5d54bb2161c2d2c6045",
}


# The reference below is copied from github.com/mohammadtavakoli78/BEAM at commit b2da22e,
# src/evaluation/compute_metrics.py, under the MIT license in bench/beam_prompts/LICENSE.
# Only the type hints and the line breaks differ.
def official_parse_json_response(response: str) -> Any:
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
        json_part = match.group(1)
        try:
            return json.loads(json_part)
        except Exception as e:
            raise ValueError(f"Found possible JSON but failed to parse it: {e}") from e
    raise ValueError("No valid JSON found in response.")


def official_llm_equivalence(first_paragraph: str, second_paragraph: str, llm: Any) -> bool:
    system_msg = {
        "role": "system",
        "content": """
            You are a binary classifier.
            If the TWO snippets describe the SAME event/fact, reply **YES**
            Otherwise reply **NO**. No extra words.
            DO NOT provide any exaplanation.
        """,
    }
    user_msg = {
        "role": "user",
        "content": f"""First snippet: {first_paragraph} \n
                       Second snippet: {second_paragraph}
                    """,
    }
    messages = [system_msg, user_msg]
    response: str = llm.invoke(messages).content.lower()
    return "yes" in response


def official_align_with_llm(
    reference: list[str], system: list[str], llm: Any
) -> tuple[list[str], list[str]]:
    used: set[int] = set()
    system_out = []
    for s in system:
        matched_index = None
        for index, r in enumerate(reference):
            if index in used:
                continue
            if official_llm_equivalence(first_paragraph=r, second_paragraph=s, llm=llm):
                matched_index = index
                break
        if matched_index is not None:
            system_out.append(reference[matched_index])
            used.add(matched_index)
        else:
            system_out.append(s)
    return reference, system_out


def official_event_ordering_score(
    reference_list: list[str], system_list: list[str], llm: Any
) -> dict[str, float]:
    reference_canon, system_canon = official_align_with_llm(reference_list, system_list, llm)
    tp = len(set(reference_canon) & set(system_canon))
    fp = len([x for x in system_canon if x not in reference_canon])
    fn = len([x for x in reference_canon if x not in system_canon])
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
    union = list(dict.fromkeys(reference_canon + system_canon))
    tie_rank = len(union) + 1

    def to_rank(seq: list[str]) -> list[int]:
        r = {item: i + 1 for i, item in enumerate(seq)}
        return [r.get(u, tie_rank) for u in union]

    tau_b, p_value = kendalltau(
        to_rank(reference_canon), to_rank(system_canon), variant="b", method="auto"
    )
    tau_b_norm = (tau_b + 1) / 2 if tau_b is not None else 0
    final_score = tau_b_norm * f1
    return dict(
        precision=precision, recall=recall, f1=f1, tau_norm=tau_b_norm, final_score=final_score
    )


def official_nugget_score(rubric: list[str], llm_response: str, llm: Any) -> float:
    score = 0.0
    for item in rubric:
        prompt = beam_score.JUDGE_PROMPT.replace("<rubric_item>", item).replace(
            "<llm_response>", llm_response
        )
        response = llm.invoke(prompt).content.strip()
        try:
            response = official_parse_json_response(response=response)
        except:  # noqa: E722
            response = json.loads(repair_json(response))
        score += float(response["score"])
    return score / len(rubric)


# End of the copied reference.


class Content:
    def __init__(self, content: str) -> None:
        self.content = content


class Oracle:
    """A fake model that answers a match call by whether both snippets name the same event.

    An event is a word `e<n>`. A judge call gets the next reply of `judge`.
    """

    def __init__(self, judge: Sequence[str] = ()) -> None:
        self.judge = list(judge)
        self.matches: list[tuple[str | None, str]] = []

    def answer(self, system: str | None, prompt: str) -> str:
        if system is None:
            return self.judge.pop(0)
        self.matches.append((system, prompt))
        first, second = prompt.split("Second snippet:", 1)
        left = set(re.findall(r"e\d+", first))
        right = set(re.findall(r"e\d+", second))
        return "YES" if left and left == right else "NO"

    def invoke(self, prompt: str | list[dict[str, str]]) -> Content:
        if isinstance(prompt, str):
            return Content(self.answer(None, prompt))
        return Content(self.answer(prompt[0]["content"], prompt[1]["content"]))

    def __call__(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        schema: dict[str, Any] | None = None,
        system: str | None = None,
    ) -> Reply:
        assert model == JUDGE
        return Reply(self.answer(system, prompt), 1, 1)


def probe(ability: str, rubric: Sequence[str]) -> Probe:
    return Probe("1-x-1", "1", ability, "q?", "gold", tuple(rubric), "easy", ())


def test_the_prompts_are_the_official_files() -> None:
    for name, digest in PROMPT_SHA256.items():
        path = Path(beam_score.PROMPTS) / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name
    assert "MIT License" in (Path(beam_score.PROMPTS) / "LICENSE").read_text()


def test_the_prompts_fill_their_slots() -> None:
    prompt = answer_prompt("the block", "the question?")
    assert "CONTEXT:\nthe block\n" in prompt
    assert "QUESTION:\nthe question?\n" in prompt
    judged = judge_prompt("LLM response should state: 7 days", "It is 7 days.")
    assert "- RUBRIC CRITERION (what to check): LLM response should state: 7 days" in judged
    assert "- RESPONSE TO EVALUATE: It is 7 days." in judged
    assert "<" + "rubric_item>" not in judged


REPLIES = [
    '{"score": 1.0, "reason": "ok"}',
    '```json\n{"score": 0.5, "reason": "partial"}\n```',
    'Here is my verdict: {"score": 0.0, "reason": "missing"} done',
    '{"score": 0.5, "reason": "a reason with an unclosed quote}',
    '  {"score": 1, "reason": "spaces"}  ',
]


@pytest.mark.parametrize("text", REPLIES)
def test_the_judge_parse_matches_the_official_one(text: str) -> None:
    try:
        expected = float(official_parse_json_response(text)["score"])
    except ValueError:
        expected = float(json.loads(repair_json(text))["score"])
    assert judge_reply(text) == expected


def test_parse_raises_without_json() -> None:
    with pytest.raises(ValueError, match="No valid JSON"):
        parse_json_response("no json here")


def test_a_nugget_question_scores_the_mean_as_the_official_code() -> None:
    rubric = ["n1", "n2", "n3", "n4", "n5"]
    found = score(Oracle(REPLIES), probe("temporal_reasoning", rubric), "resp")
    assert found.nuggets == (1.0, 0.5, 0.0, 0.5, 1.0)
    assert found.score == found.nugget == official_nugget_score(rubric, "resp", Oracle(REPLIES))
    assert found.tau_norm is None


def random_case(rng: random.Random) -> tuple[list[str], str]:
    events = [f"e{n}" for n in range(rng.randint(2, 8))]
    reference = [f"Event {e} happened" for e in events]
    shown = rng.sample(events, rng.randint(0, len(events)))
    lines = [f"- then {e}" for e in shown]
    for _ in range(rng.randint(0, 3)):
        lines.insert(rng.randint(0, len(lines)), rng.choice(["", "Here is the order:", "e99"]))
    if rng.random() < 0.2 and lines:
        lines.append(lines[0])
    return reference, "\n".join(lines)


def test_event_ordering_matches_the_official_code_on_many_replies() -> None:
    rng = random.Random(20261026)
    for _ in range(300):
        reference, response = random_case(rng)
        ours, theirs = Oracle(), Oracle()
        reference_out, system_out = align(ours, reference, response.split("\n"))
        expected = official_event_ordering_score(reference, response.split("\n"), theirs)
        found = order_score(reference_out, system_out)
        assert ours.matches == theirs.matches
        for key, value in expected.items():
            if math.isnan(value):
                assert math.isnan(found[key])
            else:
                assert found[key] == pytest.approx(value), (key, reference, response)


def test_event_ordering_scores_tau_norm_and_keeps_the_nugget_mean() -> None:
    rubric = ["Event e1 happened", "Event e2 happened", "Event e3 happened"]
    response = "Order:\n- e2\n- e1\n- e3"
    model = Oracle(['{"score": 1}', '{"score": 1}', '{"score": 0}'])
    found = score(model, probe("event_ordering", rubric), response)
    expected = official_event_ordering_score(rubric, response.split("\n"), Oracle())
    assert found.score == found.tau_norm == pytest.approx(expected["tau_norm"])
    assert found.f1 == pytest.approx(expected["f1"])
    assert found.nugget == pytest.approx(2 / 3)
    assert all(system == MATCH_SYSTEM for system, _ in model.matches)


def test_summarize_takes_the_mean_over_the_abilities() -> None:
    rows = [
        {"ability": "abstention", "domain": "Coding", "score": 1.0},
        {"ability": "abstention", "domain": "Math", "score": 0.0},
        {"ability": "summarization", "domain": "Coding", "score": 1.0},
    ]
    found = summarize(rows)
    assert found["by_ability"] == {"abstention": 0.5, "summarization": 1.0}
    assert found["mean"] == 0.75
    assert found["questions"] == pytest.approx(2 / 3)
    assert found["by_domain"] == {"Coding": 1.0, "Math": 0.0}
