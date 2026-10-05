"""The frozen surfaces. Each test compares a public surface against a literal set."""

from dataclasses import fields
from typing import get_args

import hotmemory
from hotmemory import Filter, Kind, Record, Store


def changed(surface: str) -> str:
    return (
        f"{surface} is a frozen surface. A change to it is a public contract change: "
        "add an entry to CHANGELOG.md under Unreleased, then update the literal in this test."
    )


def test_all_is_frozen() -> None:
    assert set(hotmemory.__all__) == {
        "SCHEMA_VERSION",
        "Clock",
        "Embedder",
        "Filter",
        "Hit",
        "JSONValue",
        "Kind",
        "MemoryStore",
        "MemoryWriter",
        "Record",
        "Store",
        "TimeRange",
        "Writer",
        "normalize",
    }, changed("hotmemory.__all__")


def test_store_methods_are_frozen() -> None:
    methods = {name for name, value in vars(Store).items() if callable(value)}
    public = {name for name in methods if not name.startswith("_")}
    assert public == {
        "put",
        "get",
        "history",
        "list",
        "search",
        "delete",
        "list_namespaces",
        "writer",
    }, changed("The method set of Store")


def test_record_fields_are_frozen() -> None:
    assert hotmemory.SCHEMA_VERSION == 1, changed("The schema version")
    assert {field.name: field.type for field in fields(Record)} == {
        "namespace": "tuple[str, ...]",
        "key": "str",
        "revision": "int",
        "kind": "Kind",
        "subject": "str",
        "content": "str",
        "cues": "tuple[str, ...]",
        "payload": "dict[str, JSONValue]",
        "tags": "tuple[str, ...]",
        "sources": "tuple[str, ...]",
        "actor": "str",
        "created_at": "datetime",
        "observed_at": "datetime | None",
        "valid_from": "datetime | None",
        "valid_until": "datetime | None",
        "expired_at": "datetime | None",
        "superseded_by": "str | None",
        "forget_after": "datetime | None",
        "forget_reason": "str",
        "id": "str",
    }, changed("The fields and field types of Record, schema version 1,")
    assert set(get_args(Kind)) == {"fact", "profile", "procedure", "episode"}, changed(
        "The values of Kind"
    )


def test_filter_keys_are_frozen() -> None:
    assert {field.name for field in fields(Filter)} == {
        "kind",
        "subject",
        "tags",
        "actor",
        "valid_from",
        "valid_until",
        "created_at",
        "expired_at",
    }, changed("The filter keys of list and search")
