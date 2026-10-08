"""`MemoryStore`: the in-process driver, and the reference for every other driver."""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from hotmemory._rules import (
    build_record,
    check_episode_line,
    check_prefix,
    cosine_distance,
    is_duplicate,
    is_forgotten,
    is_listed,
    matches,
    newest_first,
    next_revision,
    under_prefix,
)
from hotmemory._writer import BufferedWriter, check_count
from hotmemory.filter import Filter
from hotmemory.record import JSONValue, Kind, Record, check_key, check_namespace
from hotmemory.store import Clock, Embedder, Hit


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


class MemoryStore:
    """A `Store` that holds every revision in process memory.

    `embedder` turns texts into vectors for `search` with query text. Without one, such a
    search raises RuntimeError. `clock` gives `created_at` and the time against which
    `forget_after` is compared. Search ranks by the cosine distance between the query and
    `content`.
    """

    def __init__(self, *, embedder: Embedder | None = None, clock: Clock = utc_now) -> None:
        self._embedder = embedder
        self._clock = clock
        self._revisions: dict[tuple[tuple[str, ...], str], builtins.list[Record]] = {}
        self._vectors: dict[str, tuple[float, ...]] = {}

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
    ) -> str:
        draft = build_record(
            namespace,
            key,
            1,
            self._clock(),
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
        return self._write(draft)

    def get(self, namespace: Sequence[str], key: str, revision: int | None = None) -> Record | None:
        revisions = self._revisions.get(self._slot(namespace, key), [])
        if revision is None:
            found = revisions[-1] if revisions else None
        else:
            found = next((record for record in revisions if record.revision == revision), None)
        return None if found is None else _copy(found)

    def history(self, namespace: Sequence[str], key: str) -> builtins.list[Record]:
        return [_copy(record) for record in self._revisions.get(self._slot(namespace, key), [])]

    def list(
        self,
        prefix: Sequence[str],
        filter: Filter | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> builtins.list[Record]:
        check_count("limit", limit)
        records = self._listed([prefix], filter)
        if since is not None:
            records = [record for record in records if record.created_at >= since]
        return [_copy(record) for record in records[:limit]]

    def search(
        self,
        query: str | None,
        prefixes: Sequence[Sequence[str]],
        filter: Filter | None = None,
        k: int = 10,
    ) -> builtins.list[Hit]:
        check_count("k", k)
        records = self._listed(prefixes, filter)
        if query is None:
            return [Hit(_copy(record), None) for record in records[:k]]
        query_vector = self._embed_query(query)
        self._embed_missing(records)
        hits = [
            Hit(record, cosine_distance(query_vector, self._vectors[record.id]))
            for record in records
        ]
        hits.sort(key=lambda hit: (hit.distance, newest_first(hit.record)))
        return [Hit(_copy(hit.record), hit.distance) for hit in hits[:k]]

    def delete(self, namespace: Sequence[str], key: str) -> None:
        for record in self._revisions.pop(self._slot(namespace, key), []):
            self._vectors.pop(record.id, None)

    def list_namespaces(self, prefix: Sequence[str] = ()) -> builtins.list[tuple[str, ...]]:
        labels = check_prefix(prefix)
        return sorted(
            {namespace for namespace, _ in self._revisions if under_prefix(namespace, labels)}
        )

    def writer(
        self, max_rows: int = 1000, interval: timedelta = timedelta(seconds=5)
    ) -> MemoryWriter:
        return MemoryWriter(self, max_rows, interval)

    def sweep(self) -> builtins.list[str]:
        now = self._clock()
        swept = [
            slot for slot, revisions in self._revisions.items() if is_forgotten(revisions[-1], now)
        ]
        ids = []
        for slot in swept:
            for record in self._revisions.pop(slot):
                self._vectors.pop(record.id, None)
                ids.append(record.id)
        return sorted(ids)

    def _write(self, draft: Record) -> str:
        slot = (draft.namespace, draft.key)
        revisions = self._revisions.get(slot, [])
        current = revisions[-1] if revisions else None
        check_episode_line(current, draft.kind)
        now = self._clock()
        if current is not None and is_duplicate(current, draft.content, now):
            return current.id
        record = replace(draft, revision=next_revision(current), created_at=now)
        if current is not None:
            revisions = [*revisions[:-1], replace(current, superseded_by=record.id)]
        self._revisions[slot] = [*revisions, record]
        return record.id

    def _write_many(self, drafts: Sequence[Record]) -> builtins.list[str]:
        return [self._write(draft) for draft in drafts]

    def _slot(self, namespace: Sequence[str], key: str) -> tuple[tuple[str, ...], str]:
        labels = check_namespace(namespace)
        check_key(key)
        return labels, key

    def _listed(
        self, prefixes: Sequence[Sequence[str]], filter: Filter | None
    ) -> builtins.list[Record]:
        if isinstance(prefixes, str):
            raise TypeError("prefixes must be a sequence of prefixes, not a string")
        checked = [check_prefix(prefix) for prefix in prefixes]
        now = self._clock()
        records = [
            revisions[-1]
            for (namespace, _), revisions in self._revisions.items()
            if any(under_prefix(namespace, prefix) for prefix in checked)
            and is_listed(revisions[-1], now)
            and matches(revisions[-1], filter)
        ]
        records.sort(key=newest_first)
        return records

    def _embed_query(self, query: str) -> tuple[float, ...]:
        if self._embedder is None:
            raise RuntimeError("search with query text needs a MemoryStore with an embedder")
        return self._embed([query])[0]

    def _embed_missing(self, records: Sequence[Record]) -> None:
        missing = [record for record in records if record.id not in self._vectors]
        if not missing:
            return
        vectors = self._embed([record.content for record in missing])
        for record, vector in zip(missing, vectors, strict=True):
            self._vectors[record.id] = vector

    def _embed(self, texts: Sequence[str]) -> builtins.list[tuple[float, ...]]:
        assert self._embedder is not None
        vectors = [tuple(float(x) for x in vector) for vector in self._embedder(texts)]
        if len(vectors) != len(texts):
            raise ValueError(f"embedder returned {len(vectors)} vectors for {len(texts)} texts")
        return vectors


class MemoryWriter(BufferedWriter):
    """The `Writer` that `MemoryStore.writer` returns."""

    def __init__(self, store: MemoryStore, max_rows: int, interval: timedelta) -> None:
        super().__init__(store._write_many, store._clock, max_rows, interval)


def _copy(record: Record) -> Record:
    """Return a record equal to `record` that shares no mutable value with it."""
    return replace(record)
