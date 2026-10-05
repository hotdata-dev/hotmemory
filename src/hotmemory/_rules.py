"""Plain functions that every driver calls, so the drivers agree on the contract."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime

from hotmemory.filter import Filter
from hotmemory.record import JSONValue, Kind, Record, check_label, normalize


def check_prefix(prefix: Sequence[str]) -> tuple[str, ...]:
    """Return `prefix` as a tuple of labels. An empty prefix is allowed and matches all."""
    if isinstance(prefix, str):
        raise TypeError("prefix must be a sequence of labels, not a string")
    labels = tuple(prefix)
    for label in labels:
        check_label(label)
    return labels


def under_prefix(namespace: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    """Return True if the first labels of `namespace` are the labels of `prefix`."""
    return namespace[: len(prefix)] == prefix


def next_revision(current: Record | None) -> int:
    """Return the revision number for the next put after `current`."""
    return 1 if current is None else current.revision + 1


def is_duplicate(current: Record, content: str) -> bool:
    """Return True if `content` normalizes to the normalized content of `current`."""
    return normalize(current.content) == normalize(content)


def is_listed(record: Record, now: datetime) -> bool:
    """Return True if `list` and `search` can return `record` at `now`.

    A superseded revision is not listed. A record is not listed once its `forget_after`
    is at or before `now`.
    """
    if record.superseded_by is not None:
        return False
    return record.forget_after is None or record.forget_after > now


def matches(record: Record, filter: Filter | None) -> bool:
    """Return True if `record` satisfies every field that `filter` sets."""
    if filter is None:
        return True
    if filter.kind is not None and record.kind != filter.kind:
        return False
    if filter.subject is not None and record.subject != filter.subject:
        return False
    if filter.actor is not None and record.actor != filter.actor:
        return False
    if filter.tags is not None and not set(filter.tags) <= set(record.tags):
        return False
    ranges = (
        (filter.valid_from, record.valid_from),
        (filter.valid_until, record.valid_until),
        (filter.created_at, record.created_at),
        (filter.expired_at, record.expired_at),
    )
    return all(window is None or window.contains(value) for window, value in ranges)


def newest_first(record: Record) -> tuple[float, str]:
    """Sort key for the order of `list`: newest `created_at` first, then by id."""
    return (-record.created_at.timestamp(), record.id)


def build_record(
    namespace: Sequence[str],
    key: str,
    revision: int,
    created_at: datetime,
    *,
    kind: Kind,
    content: str,
    subject: str,
    cues: Sequence[str],
    payload: dict[str, JSONValue] | None,
    tags: Sequence[str],
    sources: Sequence[str],
    actor: str,
    observed_at: datetime | None,
    valid_from: datetime | None,
    valid_until: datetime | None,
    forget_after: datetime | None,
    forget_reason: str,
) -> Record:
    """Return the record that a put writes. `valid_from` defaults to `observed_at`."""
    return Record(
        namespace=tuple(namespace),
        key=key,
        revision=revision,
        kind=kind,
        subject=subject,
        content=content,
        cues=tuple(cues),
        payload={} if payload is None else dict(payload),
        tags=tuple(tags),
        sources=tuple(sources),
        actor=actor,
        created_at=created_at,
        observed_at=observed_at,
        valid_from=observed_at if valid_from is None else valid_from,
        valid_until=valid_until,
        forget_after=forget_after,
        forget_reason=forget_reason,
    )


def cosine_distance(a: Sequence[float], b: Sequence[float]) -> float:
    """Return 1 minus the cosine similarity of `a` and `b`.

    Raises ValueError if the lengths differ or a vector has zero length.
    """
    if len(a) != len(b):
        raise ValueError(f"vectors differ in length: {len(a)} and {len(b)}")
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        raise ValueError("cannot compute a cosine distance for a zero vector")
    return 1.0 - sum(x * y for x, y in zip(a, b, strict=True)) / (norm_a * norm_b)
