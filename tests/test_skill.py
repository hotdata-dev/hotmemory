"""The command check. Every command in the skill file runs, in order, and exits zero."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "hotmemory"
BLOCK = re.compile(r"^```sh\n(.*?)^```", re.MULTILINE | re.DOTALL)
FRONT_MATTER = re.compile(r"\A---\nname: (\S+)\ndescription: (.+?)\n---\n", re.DOTALL)


def commands() -> list[str]:
    """Return each command in the sh blocks of the skill file, with continuations joined."""
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    return [
        command
        for block in BLOCK.findall(text)
        for command in block.replace("\\\n", " ").splitlines()
        if command.strip()
    ]


def test_skill_file_has_a_name_and_a_description() -> None:
    match = FRONT_MATTER.match((SKILL / "SKILL.md").read_text(encoding="utf-8"))
    assert match is not None
    assert match.group(1) == "hotmemory"


def test_every_command_in_the_skill_file_exits_zero(tmp_path: Path) -> None:
    folder = tmp_path / "hotmemory"
    shutil.copytree(SKILL, folder, ignore=shutil.ignore_patterns("__pycache__"))
    path = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
    found = commands()
    assert len(found) == 7
    for command in found:
        result = subprocess.run(
            command,
            shell=True,
            cwd=folder,
            env={**os.environ, "PATH": path},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{command}\n{result.stderr}"
    records = (folder / "memory.json").read_text(encoding="utf-8")
    assert "The disk fills at noon." in records
    assert "The CPU spikes at noon." not in records
