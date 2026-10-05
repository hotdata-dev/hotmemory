"""Fail when a relative Markdown link points at a file that does not exist.

Checks every Markdown file at the repository root and under docs/. A link
with a scheme (https:, mailto:) or a bare anchor is skipped. Links inside fenced
code blocks and inline code spans are ignored. Prints one line per broken link
and exits 1 when any is found.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FENCE = re.compile(r"^\s*(```|~~~)")
CODE_SPAN = re.compile(r"`[^`]*`")
LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def markdown_files() -> list[Path]:
    files = sorted(ROOT.glob("*.md"))
    files.extend(sorted((ROOT / "docs").rglob("*.md")))
    return files


def links(path: Path) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in LINK.finditer(CODE_SPAN.sub("", line)):
            found.append((number, match.group(1)))
    return found


def broken(path: Path) -> list[str]:
    problems: list[str] = []
    for number, target in links(path):
        if SCHEME.match(target) or target.startswith("#"):
            continue
        file_part = target.split("#", 1)[0]
        if not (path.parent / file_part).exists():
            problems.append(f"{path.relative_to(ROOT)}:{number}: broken link {target}")
    return problems


def main() -> int:
    problems = [problem for path in markdown_files() for problem in broken(path)]
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} broken link(s)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
