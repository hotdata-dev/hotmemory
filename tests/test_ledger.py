"""The ledger test. Every row of docs/guarantees.md names a test that exists, or a phase."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "docs" / "guarantees.md"
TESTS = ROOT / "tests"
HEADER = "| Question | Answer | State | Test |"
TEST_NAME = re.compile(r"`(test_[A-Za-z0-9_]+)`")
PHASE = re.compile(r"\bphase \d+\b")


def ledger_rows() -> list[tuple[str, str]]:
    """Return the question and the Test cell of each row of the ledger table."""
    lines = LEDGER.read_text(encoding="utf-8").splitlines()
    start = lines.index(HEADER) + 2
    rows = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append((cells[0], cells[-1]))
    return rows


def defined_tests() -> set[str]:
    """Return the name of every test function in the test files."""
    return {
        node.name
        for path in sorted(TESTS.glob("test_*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }


def test_ledger_has_rows() -> None:
    assert len(ledger_rows()) >= 16


def test_every_named_test_exists() -> None:
    defined = defined_tests()
    missing = [
        f"{question} -> {name}"
        for question, cell in ledger_rows()
        for name in TEST_NAME.findall(cell)
        if name not in defined
    ]
    assert not missing, (
        "docs/guarantees.md names tests that no file in tests/ defines:\n" + "\n".join(missing)
    )


def test_every_row_names_a_test_or_a_phase() -> None:
    empty = [
        question
        for question, cell in ledger_rows()
        if not TEST_NAME.search(cell) and not PHASE.search(cell)
    ]
    assert not empty, "these ledger rows name no test and no phase:\n" + "\n".join(empty)
