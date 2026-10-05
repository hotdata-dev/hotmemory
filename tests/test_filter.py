from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from hotmemory import Filter, TimeRange

NOW = datetime(2026, 10, 5, tzinfo=UTC)


def test_time_range_is_half_open() -> None:
    window = TimeRange(start=NOW, end=NOW + timedelta(days=1))
    assert window.contains(NOW)
    assert not window.contains(NOW + timedelta(days=1))
    assert not window.contains(NOW - timedelta(seconds=1))


def test_time_range_never_contains_null() -> None:
    assert not TimeRange().contains(None)


def test_open_time_range_contains_any_time() -> None:
    assert TimeRange().contains(NOW)


def test_time_range_refuses_end_before_start() -> None:
    with pytest.raises(ValueError, match="before"):
        TimeRange(start=NOW, end=NOW - timedelta(days=1))


def test_time_range_refuses_a_naive_time() -> None:
    with pytest.raises(ValueError, match="time zone"):
        TimeRange(start=datetime(2026, 10, 5))


def test_filter_refuses_an_unknown_key() -> None:
    fields: dict[str, Any] = {"payload": {}}
    with pytest.raises(TypeError):
        Filter(**fields)


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"kind": "note"}, ValueError),
        ({"subject": 1}, TypeError),
        ({"actor": 1}, TypeError),
        ({"tags": "disk"}, TypeError),
        ({"tags": [1]}, TypeError),
        ({"created_at": NOW}, TypeError),
    ],
)
def test_filter_refuses_an_unsupported_value(
    fields: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        Filter(**fields)


def test_filter_tags_become_a_tuple() -> None:
    assert Filter(tags=["disk"]).tags == ("disk",)  # type: ignore[arg-type]
