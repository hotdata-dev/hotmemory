"""The LongMemEval data, S and M: the download, its checks, and the questions in date order."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_FILE = "longmemeval_s_cleaned.json"
DATA_REVISION = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
DATA_URL = (
    "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/"
    f"{DATA_REVISION}/{DATA_FILE}"
)
DATA_SHA256 = "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442"
M_FILE = "longmemeval_m_cleaned.json"
M_SHA256 = "9d79e5524794a2e6900a3aa9cb7d9152c5a3e8319c9a87c25494ba1eacee495f"
DATASETS = {"s": (DATA_FILE, DATA_SHA256), "m": (M_FILE, M_SHA256)}
DATE_FORMAT = "%Y/%m/%d (%a) %H:%M"
END_OF_DAY = time(23, 59, 59, tzinfo=UTC)
QUESTION_TYPES = (
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
    "knowledge-update",
    "temporal-reasoning",
)
_NOT_IN_KEY = re.compile(r"[^A-Za-z0-9_-]")
_CHUNK = 1 << 20


@dataclass(frozen=True)
class Turn:
    """One turn of a session: the role, the text, and the evidence label."""

    role: str
    content: str
    has_answer: bool


@dataclass(frozen=True)
class Session:
    """One session of a haystack, with its date in UTC."""

    id: str
    date: datetime
    turns: tuple[Turn, ...]

    @property
    def key(self) -> str:
        """The episode key: the id with `-` for each character outside `[A-Za-z0-9_-]`."""
        return _NOT_IN_KEY.sub("-", self.id)

    @property
    def source(self) -> str:
        """The source of the episode: `session:<id>`."""
        return f"session:{self.id}"

    @property
    def user_text(self) -> str:
        """The user turns joined by one space, as the reference retrieval indexes them."""
        return " ".join(turn.content for turn in self.turns if turn.role == "user")

    @property
    def is_evidence(self) -> bool:
        """True if the id holds `answer` and a user turn has the answer.

        This is the rule of the reference code. It renames any other session to `noans`.
        """
        return "answer" in self.id and any(
            turn.has_answer for turn in self.turns if turn.role == "user"
        )


@dataclass(frozen=True)
class Question:
    """One question with its haystack, sorted by date, oldest first."""

    id: str
    type: str
    question: str
    answer: str
    date: datetime
    answer_session_ids: tuple[str, ...]
    sessions: tuple[Session, ...]

    @property
    def is_abstention(self) -> bool:
        """True if the id ends in `_abs`: the history does not hold the answer."""
        return "_abs" in self.id

    @property
    def as_of(self) -> datetime:
        """The end of the day of the question, in UTC."""
        return datetime.combine(self.date.date(), END_OF_DAY)

    @property
    def evidence(self) -> frozenset[str]:
        """The ids of the sessions that the reference code counts as correct."""
        return frozenset(session.id for session in self.sessions if session.is_evidence)

    @property
    def is_scored(self) -> bool:
        """True if the reference code scores the retrieval of this question.

        It skips the abstention questions and the questions with no user turn that has
        the answer.
        """
        return not self.is_abstention and bool(self.evidence)

    def episodes(self) -> list[Session]:
        """Return the sessions to store as episodes, in date order.

        A session with no user text is left out, because an episode needs content. A
        repeated id keeps its first session.
        """
        seen: set[str] = set()
        kept = []
        for session in self.sessions:
            if session.user_text.strip() and session.id not in seen:
                seen.add(session.id)
                kept.append(session)
        return kept


def parse_date(value: str) -> datetime:
    """Return a date such as `2023/05/30 (Tue) 23:40` as a datetime in UTC."""
    return datetime.strptime(value, DATE_FORMAT).replace(tzinfo=UTC)


def parse_question(entry: dict[str, Any]) -> Question:
    """Return the question that one entry of the data file holds, sessions sorted by date.

    The sort is stable, so sessions with the same date keep their order in the file.
    """
    ids, dates, bodies = (
        entry["haystack_session_ids"],
        entry["haystack_dates"],
        entry["haystack_sessions"],
    )
    if not len(ids) == len(dates) == len(bodies):
        raise ValueError(f"question {entry['question_id']}: haystack lists differ in length")
    sessions = [
        Session(
            id=session_id,
            date=parse_date(date),
            turns=tuple(
                Turn(turn["role"], turn["content"], bool(turn.get("has_answer", False)))
                for turn in body
            ),
        )
        for session_id, date, body in zip(ids, dates, bodies, strict=True)
    ]
    sessions.sort(key=lambda session: session.date)
    return Question(
        id=entry["question_id"],
        type=entry["question_type"],
        question=entry["question"],
        answer=str(entry["answer"]),
        date=parse_date(entry["question_date"]),
        answer_session_ids=tuple(entry["answer_session_ids"]),
        sessions=tuple(sessions),
    )


def sha256(path: Path) -> str:
    """Return the SHA-256 of the file at `path`, in hex."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(_CHUNK):
            digest.update(block)
    return digest.hexdigest()


def download(directory: Path = DATA_DIR, dataset: str = "s") -> Path:
    """Download the data file of `dataset` into `directory` unless it is there.

    Returns its path. Raises ValueError, and keeps no file, if the download has another
    SHA-256.
    """
    data_file, expected = DATASETS[dataset]
    url = DATA_URL.removesuffix(DATA_FILE) + data_file
    path = directory / data_file
    if path.exists():
        return path
    directory.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    with urllib.request.urlopen(url) as response, partial.open("wb") as file:
        while block := response.read(_CHUNK):
            file.write(block)
    found = sha256(partial)
    if found != expected:
        partial.unlink()
        raise ValueError(f"{url} has SHA-256 {found}, expected {expected}")
    partial.rename(path)
    return path


def load(
    path: Path, expected_sha256: str | None = DATA_SHA256, user_only: bool = False
) -> list[Question]:
    """Return the questions of the data file at `path`, in file order.

    With `user_only`, the file is read one question at a time, and each session keeps only
    its user turns. Raises ValueError if `expected_sha256` is not None and the file has
    another SHA-256, or if two questions share an id.
    """
    if expected_sha256 is not None:
        found = sha256(path)
        if found != expected_sha256:
            raise ValueError(f"{path} has SHA-256 {found}, expected {expected_sha256}")
    if user_only:
        questions = [parse_question(_user_turns(entry)) for entry in _entries(path)]
    else:
        questions = [parse_question(entry) for entry in json.loads(path.read_text())]
    ids = [question.id for question in questions]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path} repeats a question id")
    return questions


def _entries(path: Path) -> Iterator[dict[str, Any]]:
    """Yield the entries of the JSON array in the file at `path`, one at a time."""
    text = path.read_text()
    decoder = json.JSONDecoder()
    position = text.index("[") + 1
    while True:
        while position < len(text) and text[position] in " \t\r\n,":
            position += 1
        if position >= len(text) or text[position] == "]":
            return
        entry, position = decoder.raw_decode(text, position)
        yield entry


def _user_turns(entry: dict[str, Any]) -> dict[str, Any]:
    """Return `entry` with only the user turns of each session."""
    sessions = [[t for t in body if t["role"] == "user"] for body in entry["haystack_sessions"]]
    return {**entry, "haystack_sessions": sessions}
