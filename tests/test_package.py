import subprocess
import sys
from pathlib import Path

import hotmemory


def test_package_ships_type_information() -> None:
    assert (Path(hotmemory.__file__).parent / "py.typed").is_file()


def test_import_needs_no_extra() -> None:
    code = (
        "import sys, hotmemory; "
        "loaded = {'hotdata_framework', 'pyarrow', 'openai'} & set(sys.modules); "
        "sys.exit(', '.join(sorted(loaded)) or None)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, f"import hotmemory loaded {result.stderr.strip()}"
