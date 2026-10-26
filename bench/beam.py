"""The BEAM data, splits `100K` and `1M`: the download, its checks, and the windows of capture.

One row of a split is one conversation: dated batches of user and assistant turns, and 20
probing questions, 2 for each of the 10 abilities. Print the counts of a split with:

    uv run --group bench python -m bench.beam --split 1M
"""

from __future__ import annotations

import argparse
import ast
import re
import statistics
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench.data import DATA_DIR, sha256

DATA_REVISION = "3205395e897e7318c7b094ef4e6047b9b82dbb03"
DATA_URL = f"https://huggingface.co/datasets/Mohammadta/BEAM/resolve/{DATA_REVISION}/data/"
SPLITS = {
    "100K": (
        "100K-00000-of-00001.parquet",
        "c0519be25907005ba873c927c50877471d550873039d96c041554d0075a78ace",
    ),
    "1M": (
        "1M-00000-of-00001.parquet",
        "41b5acbbb55a586b1305514ef9d9fb03365d9b3331b598a1c2dd7603d93ef533",
    ),
}
ABILITIES = (
    "abstention",
    "contradiction_resolution",
    "event_ordering",
    "information_extraction",
    "instruction_following",
    "knowledge_update",
    "multi_session_reasoning",
    "preference_following",
    "summarization",
    "temporal_reasoning",
)
"""The 10 abilities, in the column order of the official report."""
GOLD_FIELDS = {
    "abstention": "ideal_response",
    "contradiction_resolution": "ideal_answer",
    "event_ordering": "answer",
    "information_extraction": "answer",
    "instruction_following": "expected_compliance",
    "knowledge_update": "answer",
    "multi_session_reasoning": "answer",
    "preference_following": "expected_compliance",
    "summarization": "ideal_summary",
    "temporal_reasoning": "answer",
}
"""The field of each ability that holds the gold answer."""
WINDOW_CHARS = 10_000
DATE_FORMAT = "%B-%d-%Y"
_MARKER = re.compile(r"\s*->->\s*\d+\s*,\s*\d+\s*$")
_PARAGRAPH = "\n\n"


@dataclass(frozen=True)
class Message:
    """One turn of a batch: its id in the conversation, its role, and its text."""

    id: int
    role: str
    content: str


@dataclass(frozen=True)
class Batch:
    """One batch of a conversation: its number from 1, its date in UTC, and its turns."""

    number: int
    date: datetime
    messages: tuple[Message, ...]


@dataclass(frozen=True)
class Probe:
    """One probing question, with its gold answer, its rubric, and its source turn ids."""

    id: str
    conversation: str
    ability: str
    question: str
    gold: str
    rubric: tuple[str, ...]
    difficulty: str
    sources: tuple[int, ...]


@dataclass(frozen=True)
class Window:
    """One `capture` call: whole turn pairs of one batch, as `role: content` paragraphs.

    `messages` holds the ids of the turns in the window. A turn pair longer than the
    limit is cut into pieces, so its ids can be in more than one window.
    """

    batch: int
    number: int
    date: datetime
    text: str
    messages: tuple[int, ...]

    @property
    def key(self) -> str:
        """The episode key: `window-<batch in 2 digits>-<number in 4 digits>`."""
        return f"window-{self.batch:02d}-{self.number:04d}"


@dataclass(frozen=True)
class Conversation:
    """One row of a split: the domain, the batches in order, and the probes."""

    id: str
    domain: str
    batches: tuple[Batch, ...]
    probes: tuple[Probe, ...]

    @property
    def chars(self) -> int:
        """The characters of every turn."""
        return sum(len(m.content) for batch in self.batches for m in batch.messages)

    def windows(self, limit: int = WINDOW_CHARS) -> list[Window]:
        """Return the windows of every batch, in order, numbered from 1 in each batch.

        A window holds whole turn pairs, a user turn and the turns after it, of at most
        `limit` characters in all. A pair longer than `limit` is cut at blank lines into
        pieces of at most `limit` characters, and each piece is packed as a pair.
        """
        found = []
        for batch in self.batches:
            parts = [
                (piece, ids)
                for text, ids in _pairs(batch.messages)
                for piece in (cut(text, limit) if len(text) > limit else [text])
            ]
            for number, (text, ids) in enumerate(_pack(parts, limit), start=1):
                found.append(Window(batch.number, number, batch.date, text, ids))
        return found


def strip_marker(text: str) -> str:
    """Return `text` without the index marker at its end, such as `->-> 1,1`."""
    return _MARKER.sub("", text)


def cut(text: str, limit: int) -> list[str]:
    """Cut `text` at blank lines into pieces of at most `limit` characters.

    A paragraph longer than `limit` is cut every `limit` characters.
    """
    pieces: list[str] = []
    current = ""
    for paragraph in text.split(_PARAGRAPH):
        while len(paragraph) > limit:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        joined = current + _PARAGRAPH + paragraph if current else paragraph
        if len(joined) > limit:
            pieces.append(current)
            current = paragraph
        else:
            current = joined
    if current:
        pieces.append(current)
    return pieces


def _pairs(messages: Sequence[Message]) -> Iterator[tuple[str, tuple[int, ...]]]:
    """Yield the text and the turn ids of each turn pair, a user turn and the turns after it."""
    pair: list[Message] = []
    for message in messages:
        if message.role == "user" and pair:
            yield _paragraphs(pair), tuple(m.id for m in pair)
            pair = []
        pair.append(message)
    if pair:
        yield _paragraphs(pair), tuple(m.id for m in pair)


def _paragraphs(messages: Sequence[Message]) -> str:
    """Return `messages` as `role: content` paragraphs, as `bench.prompts.session_text` does."""
    return _PARAGRAPH.join(f"{m.role}: {m.content.strip()}" for m in messages)


def _pack(
    parts: Sequence[tuple[str, tuple[int, ...]]], limit: int
) -> Iterator[tuple[str, tuple[int, ...]]]:
    """Join `parts` in order into windows of at most `limit` characters, one blank line apart."""
    text = ""
    ids: tuple[int, ...] = ()
    for part, part_ids in parts:
        if text and len(text) + len(_PARAGRAPH) + len(part) > limit:
            yield text, ids
            text, ids = "", ()
        text = text + _PARAGRAPH + part if text else part
        ids = ids + tuple(i for i in part_ids if i not in ids)
    if text:
        yield text, ids


def parse_date(value: str) -> datetime:
    """Return a date such as `March-15-2024` as midnight UTC."""
    return datetime.strptime(value, DATE_FORMAT).replace(tzinfo=UTC)


def parse_conversation(row: Mapping[str, Any]) -> Conversation:
    """Return the conversation of one row, with the index markers stripped from user turns.

    The date of a batch is the `time_anchor` of its first turn that has one. Raises
    ValueError if a batch has no date or two dates, or if a probe has no gold field.
    """
    conversation = str(row["conversation_id"])
    batches = []
    for number, turns in enumerate(row["chat"], start=1):
        anchors = {t["time_anchor"] for t in turns if t["time_anchor"]}
        if len(anchors) != 1:
            raise ValueError(f"conversation {conversation} batch {number}: dates {anchors}")
        messages = tuple(
            Message(
                int(t["id"]),
                t["role"],
                strip_marker(t["content"]) if t["role"] == "user" else t["content"],
            )
            for t in turns
        )
        batches.append(Batch(number, parse_date(anchors.pop()), messages))
    probes = _probes(conversation, row["probing_questions"])
    return Conversation(conversation, row["conversation_seed"]["category"], tuple(batches), probes)


def _probes(conversation: str, value: str | Mapping[str, Any]) -> tuple[Probe, ...]:
    """Return the probes of `value`, a dict or its Python literal, in the order of ABILITIES."""
    by_ability = ast.literal_eval(value) if isinstance(value, str) else value
    if set(by_ability) != set(ABILITIES):
        raise ValueError(f"conversation {conversation}: abilities {sorted(by_ability)}")
    probes = []
    for ability in ABILITIES:
        for number, entry in enumerate(by_ability[ability], start=1):
            field = GOLD_FIELDS[ability]
            if field not in entry:
                raise ValueError(f"conversation {conversation} {ability} {number}: no {field}")
            probes.append(
                Probe(
                    id=f"{conversation}-{ability}-{number}",
                    conversation=conversation,
                    ability=ability,
                    question=entry["question"],
                    gold=str(entry[field]),
                    rubric=tuple(str(item) for item in entry["rubric"]),
                    difficulty=str(entry.get("difficulty", "")),
                    sources=_ids(entry.get("source_chat_ids")),
                )
            )
    return tuple(probes)


def _ids(value: Any) -> tuple[int, ...]:
    """Return the turn ids in `value`, at any depth of lists and dicts, sorted, without repeats.

    An item that is not a whole number, such as a note, is left out.
    """
    found: set[int] = set()

    def walk(item: Any) -> None:
        if isinstance(item, Mapping):
            for inner in item.values():
                walk(inner)
        elif isinstance(item, list | tuple):
            for inner in item:
                walk(inner)
        elif isinstance(item, int) or (isinstance(item, str) and item.strip().isdigit()):
            found.add(int(item))

    walk(value)
    return tuple(sorted(found))


def download(split: str, directory: Path = DATA_DIR) -> Path:
    """Download the file of `split` into `directory` unless it is there, and return its path.

    Raises ValueError, and keeps no file, if the download has another SHA-256.
    """
    name, expected = SPLITS[split]
    path = directory / f"beam-{name}"
    if path.exists():
        return path
    directory.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    with urllib.request.urlopen(DATA_URL + name) as response, partial.open("wb") as file:
        while block := response.read(1 << 20):
            file.write(block)
    found = sha256(partial)
    if found != expected:
        partial.unlink()
        raise ValueError(f"{DATA_URL + name} has SHA-256 {found}, expected {expected}")
    partial.rename(path)
    return path


def load(path: Path, expected_sha256: str | None) -> list[Conversation]:
    """Return the conversations of the parquet file at `path`, in file order.

    Raises ValueError if `expected_sha256` is not None and the file has another SHA-256,
    or if two conversations share an id.
    """
    import pyarrow.parquet

    if expected_sha256 is not None:
        found = sha256(path)
        if found != expected_sha256:
            raise ValueError(f"{path} has SHA-256 {found}, expected {expected_sha256}")
    rows = pyarrow.parquet.read_table(path).to_pylist()
    conversations = [parse_conversation(row) for row in rows]
    ids = [conversation.id for conversation in conversations]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path} repeats a conversation id")
    return conversations


def load_split(split: str, directory: Path = DATA_DIR) -> list[Conversation]:
    """Download the file of `split` if it is not there, and return its conversations."""
    return load(download(split, directory), SPLITS[split][1])


def counts(conversations: Sequence[Conversation], limit: int = WINDOW_CHARS) -> dict[str, Any]:
    """Return the counts of `conversations`: questions, nuggets, characters, and windows."""
    windows = [conversation.windows(limit) for conversation in conversations]
    per_conversation = [len(found) for found in windows]
    sizes = [len(window.text) for found in windows for window in found]
    probes = [probe for conversation in conversations for probe in conversation.probes]
    long_pairs = sum(
        1
        for conversation in conversations
        for batch in conversation.batches
        for text, _ in _pairs(batch.messages)
        if len(text) > limit
    )
    return {
        "conversations": len(conversations),
        "questions": len(probes),
        "nuggets": sum(len(probe.rubric) for probe in probes),
        "characters": sum(conversation.chars for conversation in conversations),
        "windows": sum(per_conversation),
        "windows_per_conversation": {
            "min": min(per_conversation),
            "median": statistics.median(per_conversation),
            "mean": round(statistics.mean(per_conversation), 1),
            "max": max(per_conversation),
        },
        "window_chars": {
            "median": statistics.median(sizes),
            "mean": round(statistics.mean(sizes)),
            "max": max(sizes),
        },
        "long_pairs": long_pairs,
        "domains": sorted({conversation.domain for conversation in conversations}),
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Print the counts of one split."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--split", choices=sorted(SPLITS), required=True)
    args = parser.parse_args(argv)
    for name, value in counts(load_split(args.split)).items():
        print(f"{name}: {value}")


if __name__ == "__main__":
    main()
