from pathlib import Path

import hotmemory


def test_package_ships_type_information() -> None:
    assert (Path(hotmemory.__file__).parent / "py.typed").is_file()
