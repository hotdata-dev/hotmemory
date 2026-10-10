"""The driver check: compare the vector rankings of two retrieval result files.

uv run --group bench python -m bench.compare <first.json> <second.json>
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

TOLERANCE = 1e-5


def compare(
    first: dict[str, Any], second: dict[str, Any], ranking: str = "vector"
) -> dict[str, Any]:
    """Return the questions of `first` whose sessions differ from `second` in set or order.

    The check covers the questions that both files hold. `max_distance_gap` is the
    largest difference between the distances of the same session at the same rank.
    """
    other = {row["id"]: row["ranked"][ranking] for row in second["questions"]}
    differ = []
    gap = 0.0
    compared = 0
    for row in first["questions"]:
        if row["id"] not in other:
            continue
        compared += 1
        mine, theirs = row["ranked"][ranking], other[row["id"]]
        if [s for s, _ in mine] != [s for s, _ in theirs]:
            differ.append(row["id"])
            continue
        for (_, a), (_, b) in zip(mine, theirs, strict=True):
            gap = max(gap, abs(a - b))
    return {"compared": compared, "differ": differ, "max_distance_gap": gap}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args(argv)
    found = compare(json.loads(args.first.read_text()), json.loads(args.second.read_text()))
    print(json.dumps(found, indent=1))
    same = not found["differ"] and found["max_distance_gap"] <= TOLERANCE
    print("same sessions in the same order" if same else "the rankings differ")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
