"""Read and update CHANGELOG.md for a release.

Commands:

    release VERSION DATE  Move the notes under "## [Unreleased]" into a new
                          "## [VERSION] - DATE" section. Exits 1 when Unreleased
                          is empty. Does nothing when the section exists.
    notes VERSION         Print the body of the "## [VERSION]" section.
    check [BASE]          Compare the version in pyproject.toml with the one at
                          the git ref BASE (default origin/main). Exits 1 when
                          the version changed and CHANGELOG.md has no section
                          for the new version.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
PYPROJECT = ROOT / "pyproject.toml"

UNRELEASED = re.compile(r"^## \[Unreleased\][ \t]*\n(.*?)(?=^## \[|\Z)", re.M | re.S)


def section_heading(version: str) -> re.Pattern[str]:
    return re.compile(rf"^## \[{re.escape(version)}\].*$", re.M)


def release(text: str, version: str, date: str) -> str:
    """Return `text` with the Unreleased notes moved under a new version section."""
    if section_heading(version).search(text):
        return text
    match = UNRELEASED.search(text)
    if match is None:
        raise SystemExit("CHANGELOG.md has no '## [Unreleased]' section")
    body = match.group(1).strip()
    if not body:
        raise SystemExit("CHANGELOG.md has no notes under '## [Unreleased]'")
    section = f"## [Unreleased]\n\n## [{version}] - {date}\n\n{body}\n\n"
    return text[: match.start()] + section + text[match.end() :].lstrip("\n")


def notes(text: str, version: str) -> str:
    """Return the body of the section for `version`, without its heading."""
    match = section_heading(version).search(text)
    if match is None:
        raise SystemExit(f"CHANGELOG.md has no '## [{version}]' section")
    rest = text[match.end() :]
    following = re.search(r"^## \[", rest, re.M)
    body = rest[: following.start()] if following else rest
    return body.strip() or f"Release {version}."


def project_version(pyproject: str) -> str:
    version: str = tomllib.loads(pyproject)["project"]["version"]
    return version


def check(base: str) -> str:
    """Return a status line, or exit 1 when a version change has no changelog section."""
    try:
        previous = subprocess.run(
            ["git", "show", f"{base}:pyproject.toml"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError:
        return f"skip: no pyproject.toml at {base}"
    old = project_version(previous)
    new = project_version(PYPROJECT.read_text(encoding="utf-8"))
    if old == new:
        return f"version unchanged ({new})"
    if not section_heading(new).search(CHANGELOG.read_text(encoding="utf-8")):
        raise SystemExit(
            f"pyproject.toml version changed to {new}, but CHANGELOG.md has no '## [{new}]' section"
        )
    return f"release metadata ok for {new}"


def main(argv: list[str]) -> int:
    match argv:
        case ["release", version, date]:
            text = CHANGELOG.read_text(encoding="utf-8")
            CHANGELOG.write_text(release(text, version, date), encoding="utf-8")
        case ["notes", version]:
            print(notes(CHANGELOG.read_text(encoding="utf-8"), version))
        case ["check"]:
            print(check("origin/main"))
        case ["check", base]:
            print(check(base))
        case _:
            print(__doc__, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
