"""The record of schema version 1, and the normalization that deduplication uses."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, TypeAlias

SCHEMA_VERSION = 1

Kind: TypeAlias = Literal["fact", "profile", "procedure", "episode"]
KINDS: tuple[Kind, ...] = ("fact", "profile", "procedure", "episode")

JSONValue: TypeAlias = "str | int | float | bool | None | list[JSONValue] | dict[str, JSONValue]"


def normalize(content: str) -> str:
    """Return `content` in lowercase with each run of whitespace changed to one space."""
    return " ".join(content.lower().split())


def check_namespace(namespace: Sequence[str]) -> tuple[str, ...]:
    """Return `namespace` as a tuple, or raise ValueError if a label is not allowed.

    A namespace has at least one label. A label is a non-empty string with no `.` and
    no `/`.
    """
    if isinstance(namespace, str):
        raise TypeError("namespace must be a sequence of labels, not a string")
    labels = tuple(namespace)
    if not labels:
        raise ValueError("namespace must have at least one label")
    for label in labels:
        check_label(label)
    return labels


def check_label(label: str) -> None:
    """Raise ValueError if `label` is empty or contains `.` or `/`."""
    if not isinstance(label, str):
        raise TypeError(f"namespace label must be a string, got {type(label).__name__}")
    if not label:
        raise ValueError("namespace label must not be empty")
    if "." in label or "/" in label:
        raise ValueError(f"namespace label must not contain '.' or '/': {label!r}")


def check_key(key: str) -> None:
    """Raise ValueError if `key` is empty or contains `/` or `@`."""
    if not isinstance(key, str):
        raise TypeError(f"key must be a string, got {type(key).__name__}")
    if not key:
        raise ValueError("key must not be empty")
    if "/" in key or "@" in key:
        raise ValueError(f"key must not contain '/' or '@': {key!r}")


def record_id(namespace: Sequence[str], key: str, revision: int) -> str:
    """Return the id `namespace/key@revision`, with the labels joined by `/`."""
    return f"{'/'.join(namespace)}/{key}@{revision}"


@dataclass(frozen=True, kw_only=True)
class Record:
    """One revision of a memory record, schema version 1.

    The constructor refuses a value that the contract does not allow, and derives `id`.
    Every timestamp must carry a time zone. `content` must not be empty after
    normalization. The constructor stores a deep copy of `payload`.
    """

    namespace: tuple[str, ...]
    key: str
    revision: int
    kind: Kind
    subject: str = ""
    content: str
    cues: tuple[str, ...] = ()
    payload: dict[str, JSONValue] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    actor: str = ""
    created_at: datetime
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    expired_at: datetime | None = None
    superseded_by: str | None = None
    forget_after: datetime | None = None
    forget_reason: str = ""
    id: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "namespace", check_namespace(self.namespace))
        check_key(self.key)
        if isinstance(self.revision, bool) or not isinstance(self.revision, int):
            raise TypeError("revision must be an integer")
        if self.revision < 1:
            raise ValueError(f"revision must be 1 or more, got {self.revision}")
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}, got {self.kind!r}")
        for name in ("subject", "content", "actor", "forget_reason"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"{name} must be a string")
        for name in ("cues", "tags", "sources"):
            object.__setattr__(self, name, _strings(name, getattr(self, name)))
        if not normalize(self.content):
            raise ValueError("content must not be empty")
        if not isinstance(self.payload, dict):
            raise TypeError("payload must be a dict")
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))
        if self.created_at is None:
            raise TypeError("created_at must be a datetime")
        for name in (
            "created_at",
            "observed_at",
            "valid_from",
            "valid_until",
            "expired_at",
            "forget_after",
        ):
            _check_time(name, getattr(self, name))
        if self.forget_reason and self.forget_after is None:
            raise ValueError("forget_reason must be empty when forget_after is None")
        object.__setattr__(self, "id", record_id(self.namespace, self.key, self.revision))


def _strings(name: str, values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, str):
        raise TypeError(f"{name} must be a sequence of strings, not a string")
    result = tuple(values)
    if not all(isinstance(value, str) for value in result):
        raise TypeError(f"{name} must hold only strings")
    return result


def _check_time(name: str, value: datetime | None) -> None:
    if value is None:
        return
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must carry a time zone")
