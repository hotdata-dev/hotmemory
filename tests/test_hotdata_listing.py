"""Offline tests of the database listing of `HotdataStore.provision`, with a fake client."""

from types import SimpleNamespace
from typing import Any

from hotdata_framework import ManagedDatabase

from hotmemory import hotdata


class FakeDatabases:
    """Serves `names` in pages of `size`, newest first, with the search of the API."""

    def __init__(self, names: list[str], size: int) -> None:
        self.names = names
        self.size = size
        self.calls: list[str | None] = []

    def list_databases(self, limit: int, cursor: str | None, search: str) -> Any:
        self.calls.append(cursor)
        matches = [
            SimpleNamespace(id=f"db-{n}", name=name)
            for n, name in enumerate(self.names)
            if search.lower() in name.lower()
        ]
        start = int(cursor or 0)
        end = start + min(limit, self.size)
        more = end < len(matches)
        return SimpleNamespace(
            databases=matches[start:end], has_more=more, next_cursor=str(end) if more else None
        )


class FakeClient:
    api = None

    def resolve_managed_database(self, database_id: str) -> ManagedDatabase:
        return ManagedDatabase(id=database_id, description="", default_connection_id="c")


def test_named_reads_every_page(monkeypatch: Any) -> None:
    names = [f"other-{n}" for n in range(150)] + ["store", "store-2", "store"]
    fake = FakeDatabases(names, size=100)
    monkeypatch.setattr(hotdata, "DatabasesApi", lambda api: fake)
    found = hotdata._named(FakeClient(), "store")  # type: ignore[arg-type]
    assert [db.id for db in found] == ["db-150", "db-152"]
    assert fake.calls == [None]


def test_named_follows_the_cursor(monkeypatch: Any) -> None:
    names = ["store"] * 3 + ["store-old"] * 150 + ["store"]
    fake = FakeDatabases(names, size=100)
    monkeypatch.setattr(hotdata, "DatabasesApi", lambda api: fake)
    found = hotdata._named(FakeClient(), "store")  # type: ignore[arg-type]
    assert [db.id for db in found] == ["db-0", "db-1", "db-2", "db-153"]
    assert fake.calls == [None, "100"]
