"""The reader and judge prompts of the reference code, and the prompt of the extractor.

The reader templates follow `src/generation/run_generation.py` with chain of thought, and
the judge templates follow `src/evaluation/evaluate_qa.py`, of the LongMemEval repository.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from bench.data import DATE_FORMAT, Question, Session
from bench.models import EXTRACTOR

READ_CHATS = (
    "I will give you several history chats between you and a user. Please answer the "
    "question based on the relevant chat history. Answer the question step by step: first "
    "extract all the relevant information, and then reason over the information to get the "
    "answer.\n\n\nHistory Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\nAnswer (step by step):"
)
READ_FACTS = (
    "I will give you several facts extracted from history chats between you and a user. "
    "Please answer the question based on the relevant facts. Answer the question step by "
    "step: first extract all the relevant information, and then reason over the information "
    "to get the answer.\n\n\nHistory Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\n"
    "Answer (step by step):"
)
_JUDGE_PLAIN = (
    "I will give you a question, a correct answer, and a response from a model. Please answer "
    "yes if the response contains the correct answer. Otherwise, answer no. If the response is "
    "equivalent to the correct answer or contains all the intermediate steps to get the correct "
    "answer, you should also answer yes. If the response only contains a subset of the "
    "information required by the answer, answer no. "
)
_JUDGE_TAIL = (
    "\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
    "Is the model response correct? Answer yes or no only."
)
JUDGE = {
    "single-session-user": _JUDGE_PLAIN + _JUDGE_TAIL,
    "single-session-assistant": _JUDGE_PLAIN + _JUDGE_TAIL,
    "multi-session": _JUDGE_PLAIN + _JUDGE_TAIL,
    "temporal-reasoning": _JUDGE_PLAIN
    + "In addition, do not penalize off-by-one errors for the number of days. If the "
    "question asks for the number of days/weeks/months, etc., and the model makes off-by-one "
    "errors (e.g., predicting 19 days when the answer is 18), the model's response is still "
    "correct. " + _JUDGE_TAIL,
    "knowledge-update": "I will give you a question, a correct answer, and a response from a "
    "model. Please answer yes if the response contains the correct answer. Otherwise, answer "
    "no. If the response contains some previous information along with an updated answer, the "
    "response should be considered as correct as long as the updated answer is the required "
    "answer." + _JUDGE_TAIL,
    "single-session-preference": "I will give you a question, a rubric for desired "
    "personalized response, and a response from a model. Please answer yes if the response "
    "satisfies the desired response. Otherwise, answer no. The model does not need to reflect "
    "all the points in the rubric. The response is correct as long as it recalls and utilizes "
    "the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\n"
    "Model Response: {}\n\nIs the model response correct? Answer yes or no only.",
}
JUDGE_ABSTENTION = (
    "I will give you an unanswerable question, an explanation, and a response from a model. "
    "Please answer yes if the model correctly identifies the question as unanswerable. The "
    "model could say that the information is incomplete, or some other information is given "
    "but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: "
    "{}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no "
    "only."
)
EXTRACT = """You read one chat session between a user and an assistant, and you write down \
the facts that a personal assistant must remember about the user for later conversations.

Write each fact as one short sentence that stands alone. Name the user as "the user". Keep \
names, numbers, places, dates, and amounts exactly as the session gives them. If the user \
says when something happened, keep that time in the fact. Include facts that the assistant \
told the user, if the user can ask about them later, such as a recommendation, a list, or a \
number. Leave out small talk and general knowledge that does not concern the user.

The memory already holds the facts below. Do not repeat one of them. If the session changes \
one of them, write the new fact in full.

Facts already in memory:
{known}

Give `subject` as a short label of what the fact is about, such as "car" or "sister's \
wedding". Return an empty list if the session holds no fact to remember.

Session date: {date}

Session:
{session}"""
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"subject": {"type": "string"}, "content": {"type": "string"}},
                "required": ["subject", "content"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["facts"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Variant:
    """An extractor variant: the prompt template and the model that reads it."""

    prompt: str
    model: str


VARIANTS = {"baseline": Variant(EXTRACT, EXTRACTOR)}
"""The extractor variants by name. `baseline` is the extractor of phase 5."""


def history(sessions: Sequence[Session]) -> str:
    """Return the sessions as the reference reader shows them, sorted by date.

    Each session is a heading with its number and date, then its turns as JSON without
    the evidence labels.
    """
    parts = []
    for number, session in enumerate(sorted(sessions, key=lambda s: s.date), start=1):
        turns = json.dumps([{"role": t.role, "content": t.content} for t in session.turns])
        parts.append(
            f"\n### Session {number}:\nSession Date: {session.date.strftime(DATE_FORMAT)}\n"
            f"Session Content:\n\n{turns}\n"
        )
    return "".join(parts)


def read_chats(question: Question, sessions: Sequence[Session]) -> str:
    """Return the reader prompt over whole sessions."""
    date = question.date.strftime(DATE_FORMAT)
    return READ_CHATS.format(history(sessions), date, question.question)


def read_facts(question: Question, block: str) -> str:
    """Return the reader prompt over the block of `recall`."""
    date = question.date.strftime(DATE_FORMAT)
    return READ_FACTS.format(block, date, question.question)


def judge(question: Question, response: str) -> str:
    """Return the judge prompt for the type of `question`, or the abstention prompt."""
    template = JUDGE_ABSTENTION if question.is_abstention else JUDGE[question.type]
    return template.format(question.question, question.answer, response)


def session_text(session: Session) -> str:
    """Return the turns of `session` as `role: content` lines, one blank line apart."""
    return "\n\n".join(f"{turn.role}: {turn.content.strip()}" for turn in session.turns)


def extract(session: str, date: str, known: Sequence[str], template: str = EXTRACT) -> str:
    """Return the extractor prompt for one session and the facts already in memory."""
    listed = "\n".join(known) if known else "(none)"
    return template.format(known=listed, date=date, session=session)
