"""Plain functions that every driver calls, so the drivers agree on the contract."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from hotmemory.filter import Filter, kinds_of
from hotmemory.record import JSONValue, Kind, Record, check_label, normalize


@dataclass(frozen=True)
class Draft:
    """One put before a driver writes it: the record, and whether it closes the current one."""

    record: Record
    close_previous: bool = False


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


def check_episode_line(current: Record | None, kind: Kind) -> None:
    """Raise ValueError if a put of `kind` moves the key of `current` across the episode line.

    A key holds episodes only, or holds no episode at all.
    """
    if current is not None and (current.kind == "episode") != (kind == "episode"):
        raise ValueError(
            f"key {current.key!r} holds kind {current.kind!r}; a put of kind {kind!r} "
            "cannot move a key between 'episode' and another kind"
        )


def is_duplicate(current: Record, content: str, now: datetime) -> bool:
    """Return True if a put of `content` writes nothing because `current` holds it.

    That is the case when `current` is listed at `now` and its normalized content equals
    the normalized `content`. A current revision past its `forget_after` is never a
    duplicate, so a put brings the fact back as a new revision.
    """
    return is_listed(current, now) and normalize(current.content) == normalize(content)


def check_close(current: Record, record: Record) -> None:
    """Raise ValueError if a put with `close_previous` cannot close `current` for `record`.

    The new `valid_from` must be set, and must not be earlier than the `valid_from` of
    `current`.
    """
    if record.valid_from is None:
        raise ValueError("close_previous needs valid_from or observed_at on the new revision")
    if current.valid_from is not None and current.valid_from > record.valid_from:
        raise ValueError(
            f"key {current.key!r} is valid from {current.valid_from.isoformat()}, which is "
            f"later than the new valid_from {record.valid_from.isoformat()}"
        )


def superseded(current: Record, record: Record, now: datetime, close_previous: bool) -> Record:
    """Return `current` with `superseded_by` set to the id of `record`.

    With `close_previous`, its `valid_until` also becomes the `valid_from` of `record`, and
    its `expired_at` becomes `now`.
    """
    if not close_previous:
        return replace(current, superseded_by=record.id)
    return replace(current, superseded_by=record.id, valid_until=record.valid_from, expired_at=now)


def with_new_sources(current: Record, record: Record) -> Record | None:
    """Return `record` with the sources of `current` and then its own, or None if none is new.

    A put whose content duplicates `current` calls this. The merged sources keep their
    order and hold no repeats.
    """
    sources = tuple(dict.fromkeys((*current.sources, *record.sources)))
    if sources == current.sources:
        return None
    return replace(record, sources=sources)


def is_listed(record: Record, now: datetime) -> bool:
    """Return True if `list` and `search` can return `record` at `now`.

    A superseded revision is not listed. A record is not listed once its `forget_after`
    is at or before `now`.
    """
    return record.superseded_by is None and not is_forgotten(record, now)


def is_forgotten(record: Record, now: datetime) -> bool:
    """Return True if `record` is past its `forget_after` at `now`."""
    return record.forget_after is not None and record.forget_after <= now


def matches(record: Record, filter: Filter | None) -> bool:
    """Return True if `record` satisfies every field that `filter` sets."""
    if filter is None:
        return True
    kinds = kinds_of(filter)
    if kinds is not None and record.kind not in kinds:
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
        payload={} if payload is None else payload,
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
