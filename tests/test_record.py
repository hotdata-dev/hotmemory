from datetime import UTC, datetime
from typing import Any

import pytest

from hotmemory import Record, normalize

NOW = datetime(2026, 10, 5, tzinfo=UTC)


def make(**overrides: Any) -> Record:
    fields: dict[str, Any] = {
        "namespace": ("team", "alerts"),
        "key": "disk-full",
        "revision": 1,
        "kind": "fact",
        "content": "The disk fills at night.",
        "created_at": NOW,
    }
    fields.update(overrides)
    return Record(**fields)


def test_id_is_namespace_key_and_revision() -> None:
    assert make(revision=3).id == "team/alerts/disk-full@3"


def test_defaults_are_empty() -> None:
    record = make()
    assert record.subject == ""
    assert record.cues == ()
    assert record.payload == {}
    assert record.superseded_by is None


def test_sequences_become_tuples() -> None:
    record = make(namespace=["team"], tags=["disk"], cues=["why"], sources=["thread-1"])
    assert record.namespace == ("team",)
    assert record.tags == ("disk",)
    assert record.cues == ("why",)
    assert record.sources == ("thread-1",)


@pytest.mark.parametrize("label", ["a.b", "a/b", ""])
def test_refuses_a_bad_namespace_label(label: str) -> None:
    with pytest.raises(ValueError, match="label"):
        make(namespace=("team", label))


def test_refuses_an_empty_namespace() -> None:
    with pytest.raises(ValueError, match="at least one label"):
        make(namespace=())


def test_refuses_a_string_namespace() -> None:
    with pytest.raises(TypeError, match="not a string"):
        make(namespace="team")


@pytest.mark.parametrize("key", ["a/b", "a@b", ""])
def test_refuses_a_bad_key(key: str) -> None:
    with pytest.raises(ValueError, match="key"):
        make(key=key)


@pytest.mark.parametrize("revision", [0, -1])
def test_refuses_a_revision_below_one(revision: int) -> None:
    with pytest.raises(ValueError, match="revision"):
        make(revision=revision)


def test_refuses_an_unknown_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        make(kind="note")


def test_refuses_a_string_for_tags() -> None:
    with pytest.raises(TypeError, match="tags"):
        make(tags="disk")


def test_refuses_a_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="time zone"):
        make(observed_at=datetime(2026, 10, 5))


def test_refuses_a_forget_reason_without_forget_after() -> None:
    with pytest.raises(ValueError, match="forget_reason"):
        make(forget_reason="stale")


def test_record_is_frozen() -> None:
    record = make()
    with pytest.raises(AttributeError):
        record.content = "changed"  # type: ignore[misc]


def test_normalize_lowercases_and_collapses_whitespace() -> None:
    assert normalize("  The Disk\n\tFILLS   at night. ") == "the disk fills at night."


@pytest.mark.parametrize("content", ["", " \n\t"])
def test_refuses_empty_content(content: str) -> None:
    with pytest.raises(ValueError, match="content"):
        make(content=content)


def test_payload_is_copied_on_the_way_in() -> None:
    payload: dict[str, Any] = {"hosts": ["db-1"]}
    record = make(payload=payload)
    payload["hosts"].append("db-2")
    assert record.payload == {"hosts": ["db-1"]}
