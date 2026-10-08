"""The storage contract: the `Store` protocol that every driver implements."""

from __future__ import annotations

import builtins
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import TracebackType
from typing import Protocol, Self, TypeAlias

from hotmemory.filter import Filter
from hotmemory.record import JSONValue, Kind, Record

Embedder: TypeAlias = Callable[[Sequence[str]], Sequence[Sequence[float]]]
"""Takes a list of texts and returns one vector for each text, in the same order."""

Clock: TypeAlias = Callable[[], datetime]
"""Returns the current time, with a time zone."""


@dataclass(frozen=True)
class Hit:
    """One result of `search`: a record and its cosine distance to the query.

    A lower distance is closer. The distance is None when the search has no query text.
    """

    record: Record
    distance: float | None


class Writer(Protocol):
    """A buffer of puts that a store writes in one batch.

    A buffered put is not visible until the writer flushes. The writer flushes when the
    `with` block exits without an exception, when the buffer reaches its row count, and
    on the first put after its interval passes. If the block raises, the writer drops the
    buffer.
    """

    @property
    def flushed(self) -> builtins.list[str]:
        """The ids of every put that this writer flushed, in order."""
        ...

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
        """Buffer one put. The arguments are those of `Store.put`."""
        ...

    def flush(self) -> builtins.list[str]:
        """Write the buffer and return the ids that it wrote, in order."""
        ...

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class Store(Protocol):
    """The storage contract. Each method is described in docs/contracts.md."""

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
    ) -> str:
        """Write a new revision and return its id.

        If the key has a current revision, the new revision gets the next number and the
        current one gets `superseded_by`. If the normalized content equals that of the
        current revision, nothing is written and the current id is returned. If
        `valid_from` is None, it takes the value of `observed_at`.

        With `close_previous` and a current revision, the current revision also gets
        `valid_until` set to the new `valid_from`, and `expired_at` set to the clock's
        time. The put raises ValueError and writes nothing if the new `valid_from` is None
        or is earlier than the `valid_from` of the current revision.
        """
        ...

    def get(self, namespace: Sequence[str], key: str, revision: int | None = None) -> Record | None:
        """Return the current revision, or the named one, or None if it does not exist."""
        ...

    def history(self, namespace: Sequence[str], key: str) -> builtins.list[Record]:
        """Return every revision of the key, oldest first."""
        ...

    def list(
        self,
        prefix: Sequence[str],
        filter: Filter | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> builtins.list[Record]:
        """Return up to `limit` current revisions under `prefix`, newest first.

        `since` keeps the revisions whose `created_at` is at or after it. Ties in
        `created_at` are ordered by id.
        """
        ...

    def search(
        self,
        query: str | None,
        prefixes: Sequence[Sequence[str]],
        filter: Filter | None = None,
        k: int = 10,
    ) -> builtins.list[Hit]:
        """Return up to `k` current revisions under any of `prefixes`, most relevant first.

        Each hit carries the cosine distance between the query and `content`. Each driver
        defines relevance. With no query text, the order and the records are those of
        `list`, and each distance is None.
        """
        ...

    def delete(self, namespace: Sequence[str], key: str) -> None:
        """Remove every revision of the key."""
        ...

    def list_namespaces(self, prefix: Sequence[str] = ()) -> builtins.list[tuple[str, ...]]:
        """Return the distinct namespaces under `prefix` that hold a record, sorted."""
        ...

    def writer(self, max_rows: int = 1000, interval: timedelta = timedelta(seconds=5)) -> Writer:
        """Return a writer that buffers puts. See `Writer`."""
        ...

    def sweep(self) -> builtins.list[str]:
        """Delete every revision of each key whose current revision is past `forget_after`.

        A revision is past `forget_after` when that time is at or before the clock's time.
        Returns the ids of the deleted revisions, sorted.
        """
        ...
