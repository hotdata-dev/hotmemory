import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bench.beam import (
    ABILITIES,
    GOLD_FIELDS,
    Conversation,
    counts,
    cut,
    load,
    parse_conversation,
    strip_marker,
)


def turn(id: int, role: str, content: str, anchor: str | None = None) -> dict[str, Any]:
    return {
        "id": id,
        "role": role,
        "content": content,
        "time_anchor": anchor,
        "index": None,
        "question_type": None,
    }


def probes() -> dict[str, list[dict[str, Any]]]:
    found = {}
    for ability in ABILITIES:
        found[ability] = [
            {
                "question": f"{ability} question {n}?",
                GOLD_FIELDS[ability]: f"{ability} gold {n}",
                "rubric": [f"{ability} nugget {n}a", f"{ability} nugget {n}b"],
                "difficulty": "easy",
                "source_chat_ids": {"first": [3, 1], "second": [[1, 5]]} if n == 1 else [2],
            }
            for n in (1, 2)
        ]
    return found


def row(conversation: str = "7", chat: list[list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    if chat is None:
        chat = [
            [
                turn(0, "user", "First question? ->-> 1,1", "March-15-2024"),
                turn(1, "assistant", "First answer ->-> 1,1"),
                turn(2, "user", "Second question ->->2,3"),
                turn(3, "assistant", "Second answer"),
            ],
            [
                turn(4, "user", "Later question", "April-02-2024"),
                turn(5, "assistant", "Later answer"),
            ],
        ]
    return {
        "conversation_id": conversation,
        "conversation_seed": {"category": "Coding", "id": 1, "theme": "t", "title": "t"},
        "chat": chat,
        "probing_questions": repr(probes()),
    }


@pytest.fixture
def conversation() -> Conversation:
    return parse_conversation(row())


def test_strip_marker_removes_only_the_end_marker() -> None:
    assert strip_marker("Hello ->-> 1,1") == "Hello"
    assert strip_marker("Hello ->->12, 3 ") == "Hello"
    assert strip_marker("a ->-> b is kept") == "a ->-> b is kept"


def test_batches_have_their_dates_and_stripped_user_turns(conversation: Conversation) -> None:
    assert conversation.id == "7"
    assert conversation.domain == "Coding"
    first, later = conversation.batches
    assert (first.number, first.date) == (1, datetime(2024, 3, 15, tzinfo=UTC))
    assert later.date == datetime(2024, 4, 2, tzinfo=UTC)
    assert [m.content for m in first.messages] == [
        "First question?",
        "First answer ->-> 1,1",
        "Second question",
        "Second answer",
    ]


def test_probes_follow_the_abilities_with_one_gold_field(conversation: Conversation) -> None:
    assert len(conversation.probes) == 20
    assert [p.ability for p in conversation.probes[::2]] == list(ABILITIES)
    first = conversation.probes[0]
    assert first.id == "7-abstention-1"
    assert first.gold == "abstention gold 1"
    assert first.rubric == ("abstention nugget 1a", "abstention nugget 1b")
    assert first.sources == (1, 3, 5)
    assert conversation.probes[1].sources == (2,)
    for probe in conversation.probes:
        assert probe.gold == f"{probe.ability} gold {probe.id[-1]}"


def test_a_batch_needs_exactly_one_date() -> None:
    with pytest.raises(ValueError, match="dates"):
        parse_conversation(row(chat=[[turn(0, "user", "q"), turn(1, "assistant", "a")]]))
    two = [[turn(0, "user", "q", "March-01-2024"), turn(1, "assistant", "a", "March-02-2024")]]
    with pytest.raises(ValueError, match="dates"):
        parse_conversation(row(chat=two))


def test_a_probe_needs_its_gold_field() -> None:
    found = probes()
    del found["summarization"][0]["ideal_summary"]
    with pytest.raises(ValueError, match="no ideal_summary"):
        parse_conversation({**row(), "probing_questions": repr(found)})


def test_windows_hold_whole_pairs_under_the_limit(conversation: Conversation) -> None:
    pair = len("user: First question?\n\nassistant: First answer ->-> 1,1")
    windows = conversation.windows(limit=pair + 5)
    assert [(w.batch, w.number, w.key) for w in windows] == [
        (1, 1, "window-01-0001"),
        (1, 2, "window-01-0002"),
        (2, 1, "window-02-0001"),
    ]
    assert windows[0].text == "user: First question?\n\nassistant: First answer ->-> 1,1"
    assert windows[0].messages == (0, 1)
    assert windows[1].messages == (2, 3)
    assert windows[2].date == datetime(2024, 4, 2, tzinfo=UTC)
    whole = conversation.windows()
    assert len(whole) == 2
    assert whole[0].text.count("user: ") == 2


def test_a_long_pair_is_cut_into_pieces() -> None:
    long = "\n\n".join(["x" * 30] * 4)
    chat = [[turn(0, "user", "q", "March-01-2024"), turn(1, "assistant", long)]]
    windows = parse_conversation(row(chat=chat)).windows(limit=70)
    assert len(windows) > 1
    assert all(len(w.text) <= 70 for w in windows)
    assert all(w.messages == (0, 1) for w in windows)
    assert "".join(w.text for w in windows).count("x") == 120


def test_cut_splits_at_blank_lines_and_then_hard() -> None:
    assert cut("aa\n\nbb\n\ncc", 6) == ["aa\n\nbb", "cc"]
    assert cut("abcdefgh", 3) == ["abc", "def", "gh"]
    assert all(len(piece) <= 5 for piece in cut("a\n\n" + "b" * 12 + "\n\nc", 5))


def test_load_checks_the_hash_and_the_ids(tmp_path: Path) -> None:
    import pyarrow
    import pyarrow.parquet

    path = tmp_path / "beam.parquet"
    pyarrow.parquet.write_table(pyarrow.Table.from_pylist([row("1"), row("2")]), path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert [c.id for c in load(path, digest)] == ["1", "2"]
    with pytest.raises(ValueError, match="SHA-256"):
        load(path, "0" * 64)
    pyarrow.parquet.write_table(pyarrow.Table.from_pylist([row("1"), row("1")]), path)
    with pytest.raises(ValueError, match="repeats a conversation id"):
        load(path, None)


def test_counts(conversation: Conversation) -> None:
    found = counts([conversation])
    assert found["conversations"] == 1
    assert found["questions"] == 20
    assert found["nuggets"] == 40
    assert found["windows"] == 2
    assert found["long_pairs"] == 0
    assert found["domains"] == ["Coding"]
