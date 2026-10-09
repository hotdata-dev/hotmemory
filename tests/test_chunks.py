"""Tests of the chunking that `Memory.load` uses. They need no driver."""

import pytest

from hotmemory._chunks import chunk_document

POST_MORTEM = """# 2026-08-12 Post-mortem: the pool ran out

## Summary

The pool ran out of nodes.

## Root cause

The node group was at its ceiling.

### Detail

The ceiling was 4.

## Appendix

```sh
# list the pods

kubectl get pods
```
"""


def bodies(chunks: list[str]) -> list[str]:
    return [chunk.split("\n\n", 1)[1] for chunk in chunks]


def test_a_section_starts_with_the_title_and_its_heading_path() -> None:
    chunks = chunk_document(POST_MORTEM, 60)
    prefixes = [chunk.split("\n\n", 1)[0] for chunk in chunks]
    title = "# 2026-08-12 Post-mortem: the pool ran out"
    assert prefixes == [
        title,
        f"{title} > ## Summary",
        f"{title} > ## Root cause",
        f"{title} > ## Root cause > ### Detail",
        f"{title} > ## Appendix",
    ]
    assert bodies(chunks)[3] == "### Detail\n\nThe ceiling was 4."


def test_a_heading_inside_a_fence_is_not_a_heading() -> None:
    chunks = chunk_document(POST_MORTEM, 60)
    assert bodies(chunks)[-1] == "## Appendix\n\n```sh\n# list the pods\n\nkubectl get pods\n```"


def test_small_sections_pack_into_one_chunk() -> None:
    [chunk] = chunk_document(POST_MORTEM, 2000)
    assert chunk.startswith("# 2026-08-12 Post-mortem: the pool ran out\n\n# 2026-08-12")
    assert "## Root cause\n\nThe node group was at its ceiling.\n\n### Detail" in chunk


def test_a_long_section_is_cut_at_blank_lines_outside_fences() -> None:
    fence = "```\nline one\n\nline two\n```"
    text = f"# T\n\n## Logs\n\n{'a' * 30}\n\n{fence}\n\n{'b' * 30}\n"
    chunks = chunk_document(text, 40)
    assert chunks[0] == "# T\n\n# T"
    assert bodies(chunks[1:]) == [f"## Logs\n\n{'a' * 30}", fence, "b" * 30]
    assert all(chunk.startswith("# T > ## Logs\n\n") for chunk in chunks[1:])


def test_a_long_block_is_cut_at_line_ends() -> None:
    items = [f"- item {n:02d} of the list" for n in range(6)]
    text = "## Actions\n" + "\n".join(items)
    for body in bodies(chunk_document(text, 70)):
        assert len(body) <= 70
        assert all(line in items or line == "## Actions" for line in body.split("\n"))


def test_a_cut_table_repeats_its_header() -> None:
    rows = [f"| 10:{n:02d} | step {n} |" for n in range(6)]
    table = "\n".join(["| Time | Event |", "|---|---|", *rows])
    pieces = bodies(chunk_document(f"## Timeline\n\n{table}", 80))
    assert len(pieces) > 2
    for piece in pieces[1:]:
        assert piece.startswith("| Time | Event |\n|---|---|\n| 10:")
        assert len(piece) <= 80
    rows_seen = [line for piece in pieces for line in piece.split("\n") if "step" in line]
    assert rows_seen == rows


def test_a_line_longer_than_the_size_is_cut_at_the_size() -> None:
    [first, second] = chunk_document("x" * 25, 20)
    assert (first, second) == ("x" * 20, "x" * 5)


@pytest.mark.parametrize("text", ["", "\n\n", "  \n"])
def test_a_blank_document_gives_no_chunk(text: str) -> None:
    assert chunk_document(text, 100) == []


def test_a_document_with_no_heading_gives_chunks_with_no_prefix() -> None:
    assert chunk_document("one\r\ntwo\n\nthree", 100) == ["one\ntwo\n\nthree"]


def test_every_body_fits_the_size() -> None:
    text = POST_MORTEM * 5 + "\n" + "\n".join(f"| row {n} | value |" for n in range(40))
    for size in (40, 80, 200, 1000):
        assert all(len(body) <= size for body in bodies(chunk_document(text, size)))
