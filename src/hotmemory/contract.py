"""The memory contract: `Memory`, the operations an agent calls, over one `Store`."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from hotmemory._rules import check_prefix, under_prefix
from hotmemory._writer import check_count
from hotmemory.memory import utc_now
from hotmemory.record import JSONValue, Kind, Record, check_namespace, normalize
from hotmemory.store import Clock, Hit, Store

SUBJECT_LENGTH = 64
HASH_LENGTH = 16
DEFAULT_BUDGET = 2000
FORGET_SCAN = 10_000
_NOT_IN_KEY = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True, kw_only=True)
class Fact:
    """One structured fact for `Memory.remember`: `kind`, `content`, and optional fields.

    The fields are those of `Record` that a caller sets. The store checks them on write.
    """

    kind: Kind
    content: str
    subject: str = ""
    cues: tuple[str, ...] = ()
    payload: dict[str, JSONValue] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    forget_after: datetime | None = None
    forget_reason: str = ""


def is_valid_at(record: Record, when: datetime) -> bool:
    """Return True if `record` is valid at `when` by the as-of rule.

    `valid_from` is null or at most `when`, and `valid_until` is null or after `when`.
    """
    starts = record.valid_from is None or record.valid_from <= when
    ends = record.valid_until is None or record.valid_until > when
    return starts and ends


def render_line(record: Record) -> str:
    """Return the line of `record` in a rendered block.

    The line is `- <content> [sources: a, b] [valid: <from> to <until>]`. Each run of
    whitespace in the content becomes one space. A null `valid_from` shows as `unknown`,
    a null `valid_until` as `now`, and no sources as `none`.
    """
    content = " ".join(record.content.split())
    sources = ", ".join(record.sources) or "none"
    start = _time(record.valid_from, "unknown")
    end = _time(record.valid_until, "now")
    return f"- {content} [sources: {sources}] [valid: {start} to {end}]"


def fit_lines(lines: Sequence[str], budget: int) -> int:
    """Return how many of `lines`, from the first, fit in `budget` characters with newlines.

    The count stops at the first line that would pass the budget.
    """
    used = 0
    for count, line in enumerate(lines):
        used += len(line) + (1 if count else 0)
        if used > budget:
            return count
    return len(lines)


def derive_key(subject: str, content: str) -> str:
    """Return the key of a fact with `subject` and `content`.

    Each character of `subject` outside `[A-Za-z0-9_-]` becomes `-`, and the result keeps
    its first 64 characters. An empty subject becomes `fact`. Then come `-` and the first
    16 hex characters of the SHA-256 of the normalized content.
    """
    label = _NOT_IN_KEY.sub("-", subject)[:SUBJECT_LENGTH] or "fact"
    digest = hashlib.sha256(normalize(content).encode()).hexdigest()[:HASH_LENGTH]
    return f"{label}-{digest}"


class Memory:
    """The memory contract over one `Store`. Each operation is described in docs/contracts.md.

    `clock` gives the time that an operation needs and the caller does not pass.
    """

    def __init__(self, store: Store, *, clock: Clock = utc_now) -> None:
        self._store = store
        self._clock = clock

    @property
    def store(self) -> Store:
        """The store that this memory reads and writes."""
        return self._store

    def remember(self, facts: Sequence[Fact], scope: Sequence[str], actor: str = "") -> list[str]:
        """Write each fact under `scope` with a derived key, and return the ids, in order.

        A fact whose content equals the current revision of its key writes nothing and
        returns the current id. The facts go through one writer, so if one fact is
        refused, none is written.
        """
        namespace = check_namespace(scope)
        if not facts:
            return []
        with self._store.writer(max_rows=len(facts), interval=timedelta.max) as writer:
            for fact in facts:
                writer.put(
                    namespace,
                    derive_key(fact.subject, fact.content),
                    kind=fact.kind,
                    content=fact.content,
                    subject=fact.subject,
                    cues=fact.cues,
                    payload=fact.payload,
                    tags=fact.tags,
                    sources=fact.sources,
                    actor=actor,
                    observed_at=fact.observed_at,
                    valid_from=fact.valid_from,
                    valid_until=fact.valid_until,
                    forget_after=fact.forget_after,
                    forget_reason=fact.forget_reason,
                )
        return writer.flushed

    def recall(
        self,
        query: str,
        scopes: Sequence[Sequence[str]],
        as_of: datetime | None = None,
        budget: int = DEFAULT_BUDGET,
        k: int = 10,
    ) -> tuple[list[Record], str]:
        """Return the top current records for `query` under `scopes`, and their block.

        The search returns up to `k` current records. With `as_of`, only the ones valid at
        `as_of` remain. The block holds one line for each record, in order, and stops
        before the first line that would pass `budget` characters. The list holds the
        records of the block.
        """
        check_count("budget", budget)
        records = [hit.record for hit in self._store.search(query, scopes, k=k)]
        if as_of is not None:
            records = [record for record in records if is_valid_at(record, as_of)]
        lines = [render_line(record) for record in records]
        count = fit_lines(lines, budget)
        return records[:count], "\n".join(lines[:count])

    def candidates(self, fact: Fact, scopes: Sequence[Sequence[str]], k: int = 5) -> list[Hit]:
        """Return up to `k` current records near the content of `fact`, closest first.

        Each hit carries the cosine distance to the content. Nothing is decided or written.
        """
        hits = self._store.search(fact.content, scopes, k=k)
        return sorted(hits, key=lambda hit: hit.distance if hit.distance is not None else 0.0)

    def supersede(
        self,
        scope: Sequence[str],
        key: str,
        fact: Fact,
        valid_from: datetime | None = None,
        actor: str = "",
    ) -> str:
        """Close the current revision of `key` and write `fact` as its next revision.

        The new `valid_from` is `valid_from`, else that of `fact`, else the clock's time.
        The old revision gets `valid_until` set to it and `expired_at` set to now. Raises
        ValueError if the key has no current revision, or if the old `valid_from` is later
        than the new one. Returns the id of the current revision after the call.
        """
        if self._store.get(scope, key) is None:
            raise ValueError(f"key {key!r} has no current revision to supersede")
        start = next((t for t in (valid_from, fact.valid_from) if t is not None), self._clock())
        return self._store.put(
            scope,
            key,
            kind=fact.kind,
            content=fact.content,
            subject=fact.subject,
            cues=fact.cues,
            payload=fact.payload,
            tags=fact.tags,
            sources=fact.sources,
            actor=actor,
            observed_at=fact.observed_at,
            valid_from=start,
            valid_until=fact.valid_until,
            forget_after=fact.forget_after,
            forget_reason=fact.forget_reason,
            close_previous=True,
        )

    def forget(
        self,
        scopes: Sequence[Sequence[str]],
        *,
        ids: Sequence[str] | None = None,
        horizon: datetime | None = None,
    ) -> list[str]:
        """Delete every revision of the named keys, or of the keys due before `horizon`.

        Pass exactly one of `ids` and `horizon`. Each id names its key. With `horizon`,
        a key is due when its current revision has a `forget_after` before `horizon`.
        The candidates come from `list`, up to FORGET_SCAN records under each scope, so a
        record already past its `forget_after` is left to `Store.sweep`. Raises
        ValueError, and deletes nothing, if an id is outside `scopes`. Returns the ids of
        the deleted revisions, sorted.
        """
        if (ids is None) == (horizon is None):
            raise ValueError("pass exactly one of ids and horizon")
        allowed = _scopes(scopes)
        slots: dict[tuple[tuple[str, ...], str], None] = {}
        if ids is not None:
            if isinstance(ids, str):
                raise TypeError("ids must be a sequence of ids, not a string")
            for record_id in ids:
                namespace, key = _slot(record_id)
                if not any(under_prefix(namespace, prefix) for prefix in allowed):
                    raise ValueError(f"id {record_id!r} is outside the allowed scopes")
                slots[(namespace, key)] = None
        elif horizon is not None:
            for prefix in allowed:
                for record in self._store.list(prefix, limit=FORGET_SCAN):
                    if record.forget_after is not None and record.forget_after < horizon:
                        slots[(record.namespace, record.key)] = None
        deleted: list[str] = []
        for namespace, key in slots:
            deleted.extend(record.id for record in self._store.history(namespace, key))
            self._store.delete(namespace, key)
        return sorted(deleted)


def _scopes(scopes: Sequence[Sequence[str]]) -> list[tuple[str, ...]]:
    """Return each scope in `scopes` as a tuple of labels."""
    if isinstance(scopes, str):
        raise TypeError("scopes must be a sequence of scopes, not a string")
    return [check_prefix(scope) for scope in scopes]


def _slot(record_id: str) -> tuple[tuple[str, ...], str]:
    """Return the namespace and the key that a record id names."""
    path, separator, _ = record_id.rpartition("@")
    labels = tuple(path.split("/"))
    if not separator or len(labels) < 2:
        raise ValueError(f"not a record id: {record_id!r}")
    return labels[:-1], labels[-1]


def _time(value: datetime | None, missing: str) -> str:
    """Return `value` in ISO 8601 in UTC to the second, or `missing` if it is None."""
    if value is None:
        return missing
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
