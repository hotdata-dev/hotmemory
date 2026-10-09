"""The filter that `list` and `search` accept."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from hotmemory.record import KINDS, Kind


@dataclass(frozen=True, kw_only=True)
class TimeRange:
    """A half-open time range: `start` is inside it, `end` is not.

    A side that is None is open. A null timestamp on a record is never inside a range.
    """

    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("start", "end"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, datetime):
                raise TypeError(f"TimeRange.{name} must be a datetime")
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"TimeRange.{name} must carry a time zone")
        if self.start is not None and self.end is not None and self.end < self.start:
            raise ValueError("TimeRange.end must not be before TimeRange.start")

    def contains(self, value: datetime | None) -> bool:
        """Return True if `value` is not None and lies inside the range."""
        if value is None:
            return False
        if self.start is not None and value < self.start:
            return False
        return self.end is None or value < self.end


@dataclass(frozen=True, kw_only=True)
class Filter:
    """Exact filters for `list` and `search`. A field that is None does not filter.

    `kind` takes one kind or a tuple of kinds, and matches a record of any kind named.
    `subject` and `actor` match by equality. `tags` matches a record that holds every tag
    named. The four time fields match a record whose value lies in the range.
    """

    kind: Kind | tuple[Kind, ...] | None = None
    subject: str | None = None
    tags: tuple[str, ...] | None = None
    actor: str | None = None
    valid_from: TimeRange | None = None
    valid_until: TimeRange | None = None
    created_at: TimeRange | None = None
    expired_at: TimeRange | None = None

    def __post_init__(self) -> None:
        if self.kind is not None:
            object.__setattr__(self, "kind", _check_kind(self.kind))
        for name in ("subject", "actor"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"Filter.{name} must be a string")
        if self.tags is not None:
            if isinstance(self.tags, str):
                raise TypeError("Filter.tags must be a sequence of strings, not a string")
            tags = tuple(self.tags)
            if not all(isinstance(tag, str) for tag in tags):
                raise TypeError("Filter.tags must hold only strings")
            object.__setattr__(self, "tags", tags)
        for name in ("valid_from", "valid_until", "created_at", "expired_at"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, TimeRange):
                raise TypeError(f"Filter.{name} must be a TimeRange")


def kinds_of(filter: Filter | None) -> tuple[Kind, ...] | None:
    """Return the kinds that `filter` keeps, or None if it does not filter by kind."""
    if filter is None or filter.kind is None:
        return None
    return (filter.kind,) if isinstance(filter.kind, str) else filter.kind


def _check_kind(value: object) -> Kind | tuple[Kind, ...]:
    """Return `value` as one kind or a tuple of kinds, or raise if a kind is not allowed.

    A string stays a string. A sequence becomes a tuple, and must hold at least one kind.
    """
    if isinstance(value, str):
        return _one_kind(value)
    if not isinstance(value, Sequence):
        raise TypeError("Filter.kind must be a kind or a tuple of kinds")
    kinds = tuple(_one_kind(item) for item in value)
    if not kinds:
        raise ValueError("Filter.kind must name at least one kind")
    return kinds


def _one_kind(value: object) -> Kind:
    """Return `value` if it is one of `KINDS`, or raise ValueError."""
    for kind in KINDS:
        if value == kind:
            return kind
    raise ValueError(f"kind must be one of {', '.join(KINDS)}, got {value!r}")
