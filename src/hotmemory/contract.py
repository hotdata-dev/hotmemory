"""The memory contract: `Memory`, the operations an agent calls, over one `Store`."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from hotmemory.memory import utc_now
from hotmemory.record import JSONValue, Kind, check_namespace, normalize
from hotmemory.store import Clock, Store

SUBJECT_LENGTH = 64
HASH_LENGTH = 16
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
