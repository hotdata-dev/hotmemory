"""What every script shares: the store flags, opening `Memory`, and saving the memory file."""

from __future__ import annotations

import argparse
import json
import os
import re
import zlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import fields
from datetime import datetime
from pathlib import Path
from typing import Any

from hotmemory import Fact, Memory, MemoryStore, Record

HASHED_DIMENSIONS = 256
TIME_FIELDS = (
    "created_at",
    "observed_at",
    "valid_from",
    "valid_until",
    "expired_at",
    "forget_after",
)


def parser(description: str) -> argparse.ArgumentParser:
    """Return a parser with the flags that choose the store."""
    result = argparse.ArgumentParser(description=description)
    where = result.add_mutually_exclusive_group(required=True)
    where.add_argument("--database", help="the id of a Hotdata managed database")
    where.add_argument("--memory-file", type=Path, help="a JSON file that holds the memory")
    result.add_argument(
        "--model", default="text-embedding-3-small", help="the OpenAI model, with --database"
    )
    return result


def add_fact_flags(result: argparse.ArgumentParser) -> None:
    """Add the flags of one fact."""
    result.add_argument("--kind", default="fact", choices=["fact", "profile", "procedure"])
    result.add_argument("--content", required=True)
    result.add_argument("--subject", default="")
    result.add_argument("--source", action="append", default=[], dest="sources")
    result.add_argument("--tag", action="append", default=[], dest="tags")
    result.add_argument("--cue", action="append", default=[], dest="cues")
    result.add_argument("--observed-at", type=parse_time)
    result.add_argument("--valid-from", type=parse_time)
    result.add_argument("--valid-until", type=parse_time)


def fact(args: argparse.Namespace) -> Fact:
    """Return the fact that the flags of `add_fact_flags` describe."""
    return Fact(
        kind=args.kind,
        content=args.content,
        subject=args.subject,
        sources=tuple(args.sources),
        tags=tuple(args.tags),
        cues=tuple(args.cues),
        observed_at=args.observed_at,
        valid_from=args.valid_from,
        valid_until=args.valid_until,
    )


def scope(text: str) -> tuple[str, ...]:
    """Return the labels of a scope written as `team/alerts`."""
    return tuple(text.split("/"))


def parse_time(text: str) -> datetime:
    """Return an ISO 8601 time that carries a time zone."""
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise argparse.ArgumentTypeError(f"time needs a time zone: {text!r}")
    return value


def hashed_words(texts: Sequence[str]) -> list[list[float]]:
    """Embed each text as counts of its words, hashed into HASHED_DIMENSIONS.

    It needs no model and no network. It matches words, not meaning.
    """
    vectors = []
    for text in texts:
        vector = [0.0] * HASHED_DIMENSIONS
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[zlib.crc32(word.encode()) % HASHED_DIMENSIONS] += 1.0
        if not any(vector):
            vector[0] = 1.0
        vectors.append(vector)
    return vectors


@contextmanager
def memory(args: argparse.Namespace) -> Iterator[Memory]:
    """Open `Memory` over the store that the flags name. A memory file is saved on success."""
    if args.database:
        from hotmemory.hotdata import HotdataStore
        from hotmemory.openai import OpenAIEmbedder

        embedder = OpenAIEmbedder(args.model)
        if embedder.dimensions is None:
            raise SystemExit(f"unknown vector size for model {args.model!r}")
        store = HotdataStore.open(
            args.database, embedder=embedder, model=args.model, dimensions=embedder.dimensions
        )
        yield Memory(store)
        return
    path: Path = args.memory_file
    records = load(path) if path.exists() else []
    local = MemoryStore(embedder=hashed_words, records=records)
    yield Memory(local)
    save(path, local.records())


def load(path: Path) -> list[Record]:
    """Return the records in the memory file at `path`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Record(**_parsed(row)) for row in data["records"]]


def save(path: Path, records: Sequence[Record]) -> None:
    """Write `records` to the memory file at `path`, replacing it in one step."""
    rows = [_row(record) for record in records]
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"records": rows}, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _row(record: Record) -> dict[str, Any]:
    row = {field.name: getattr(record, field.name) for field in fields(Record) if field.init}
    for name in TIME_FIELDS:
        if row[name] is not None:
            row[name] = row[name].isoformat()
    return row


def _parsed(row: dict[str, Any]) -> dict[str, Any]:
    parsed = dict(row)
    for name in TIME_FIELDS:
        if parsed[name] is not None:
            parsed[name] = datetime.fromisoformat(parsed[name])
    return parsed
