import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("changelog", ROOT / "scripts" / "changelog.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


changelog = _load()

TEXT = """# Changelog

Each change to a public surface has an entry here.

## [Unreleased]

### Added

- `Store`.

## [0.1.0] - 2026-10-01

### Added

- `Record`.
"""


def test_release_moves_the_unreleased_notes_into_the_new_section() -> None:
    result = changelog.release(TEXT, "0.2.0", "2026-10-13")
    assert result == TEXT.replace(
        "## [Unreleased]\n\n", "## [Unreleased]\n\n## [0.2.0] - 2026-10-13\n\n"
    )


def test_release_refuses_an_empty_unreleased_section() -> None:
    first = changelog.release(TEXT, "0.2.0", "2026-10-13")
    with pytest.raises(SystemExit, match="no notes"):
        changelog.release(first, "0.3.0", "2026-10-14")


def test_release_leaves_an_existing_section_alone() -> None:
    first = changelog.release(TEXT, "0.2.0", "2026-10-13")
    assert changelog.release(first, "0.2.0", "2026-10-14") == first


def test_notes_return_the_body_of_one_section() -> None:
    assert changelog.notes(TEXT, "0.1.0") == "### Added\n\n- `Record`."
    released = changelog.release(TEXT, "0.2.0", "2026-10-13")
    assert changelog.notes(released, "0.2.0") == "### Added\n\n- `Store`."


def test_notes_refuse_a_missing_section() -> None:
    with pytest.raises(SystemExit, match=r"no '## \[9.9.9\]' section"):
        changelog.notes(TEXT, "9.9.9")


def test_the_repository_changelog_has_an_unreleased_section() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert changelog.UNRELEASED.search(text)
