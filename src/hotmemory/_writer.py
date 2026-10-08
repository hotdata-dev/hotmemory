"""The buffer behind every driver's `Writer`."""

from __future__ import annotations

import builtins
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from types import TracebackType
from typing import Self

from hotmemory._rules import Draft, build_record
from hotmemory.record import JSONValue, Kind
from hotmemory.store import Clock


def check_count(name: str, value: int) -> None:
    """Raise TypeError if `value` is not an integer, or ValueError if it is negative."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must not be negative")


class BufferedWriter:
    """A `Writer` that hands its buffer to `write` in one call on each flush.

    `write` takes the buffered drafts in order and returns the id that each one wrote.
    """

    def __init__(
        self,
        write: Callable[[Sequence[Draft]], builtins.list[str]],
        clock: Clock,
        max_rows: int,
        interval: timedelta,
    ) -> None:
        check_count("max_rows", max_rows)
        if max_rows < 1:
            raise ValueError("max_rows must be 1 or more")
        self._write = write
        self._clock = clock
        self._max_rows = max_rows
        self._interval = interval
        self._buffer: builtins.list[Draft] = []
        self._flushed: builtins.list[str] = []
        self._last_flush = clock()

    @property
    def flushed(self) -> builtins.list[str]:
        return builtins.list(self._flushed)

    def put(
        self,
        namespace: Sequence[str],
        key: str,
        *,
        kind: Kind,
        content: str,
        subject: str = "",
        cues: Sequence[str] = (),
        payload: dict[str, JSONValue] | None = None,
        tags: Sequence[str] = (),
        sources: Sequence[str] = (),
        actor: str = "",
        observed_at: datetime | None = None,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        forget_after: datetime | None = None,
        forget_reason: str = "",
        close_previous: bool = False,
    ) -> None:
        now = self._clock()
        record = build_record(
            namespace,
            key,
            1,
            now,
            kind=kind,
            content=content,
            subject=subject,
            cues=cues,
            payload=payload,
            tags=tags,
            sources=sources,
            actor=actor,
            observed_at=observed_at,
            valid_from=valid_from,
            valid_until=valid_until,
            forget_after=forget_after,
            forget_reason=forget_reason,
        )
        self._buffer.append(Draft(record, close_previous))
        if len(self._buffer) >= self._max_rows or now - self._last_flush >= self._interval:
            self.flush()

    def flush(self) -> builtins.list[str]:
        ids = self._write(self._buffer) if self._buffer else []
        self._buffer.clear()
        self._flushed.extend(ids)
        self._last_flush = self._clock()
        return ids

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is None:
            self.flush()
        else:
            self._buffer.clear()
