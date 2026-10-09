"""The chunking of a Markdown document for `Memory.load`."""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_TABLE_DELIMITER = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
PATH_SEPARATOR = " > "


@dataclass(frozen=True)
class Chunk:
    """One chunk of a document: the heading path of its first line, and its text."""

    path: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class _Line:
    text: str
    level: int
    fenced: bool


def chunk_document(text: str, chunk_chars: int) -> list[str]:
    """Return the chunks of the Markdown `text`, each with its heading prefix.

    A chunk is `<prefix>\\n\\n<body>`. The prefix is the first heading of the document,
    then the heading path of the chunk's first line, joined by ` > `, with a heading that
    repeats the one before it left out. A document with no heading gives chunks with no
    prefix. The body holds at most `chunk_chars` characters, unless one line is longer.
    A document of blank lines only gives no chunk.
    """
    lines = _scan(text)
    title = next((line.text.strip() for line in lines if line.level), "")
    chunks = []
    for chunk in pack(lines, chunk_chars):
        parts: list[str] = []
        for heading in (title, *chunk.path):
            if heading and (not parts or parts[-1] != heading):
                parts.append(heading)
        prefix = PATH_SEPARATOR.join(parts)
        chunks.append(f"{prefix}\n\n{chunk.text}" if prefix else chunk.text)
    return chunks


def pack(lines: list[_Line], chunk_chars: int) -> list[Chunk]:
    """Return the sections of `lines`, cut and packed into bodies of at most `chunk_chars`.

    The sections start at each heading outside a fence. A section that does not fit is
    cut into blocks at blank lines outside fences, and its heading stays with the block
    after it when both fit. A block that does not fit is cut at
    line ends, and a line that does not fit is cut at `chunk_chars`. A cut pipe table
    repeats its header row and delimiter row at the top of each later piece. Then the
    pieces are packed in order, joined by a blank line.
    """
    pieces: list[Chunk] = []
    for path, section in _sections(lines):
        body = _join(section)
        if not body:
            continue
        if len(body) <= chunk_chars:
            pieces.append(Chunk(path, body))
            continue
        blocks = _blocks(section)
        if len(blocks) > 1 and section[0].level and len(blocks[0]) == 1:
            joined = [*blocks[0], "", *blocks[1]]
            if len("\n".join(joined)) <= chunk_chars:
                blocks[:2] = [joined]
        for block in blocks:
            pieces.extend(Chunk(path, piece) for piece in _cut_block(block, chunk_chars))
    packed: list[Chunk] = []
    for piece in pieces:
        if packed and len(packed[-1].text) + 2 + len(piece.text) <= chunk_chars:
            packed[-1] = Chunk(packed[-1].path, f"{packed[-1].text}\n\n{piece.text}")
        else:
            packed.append(piece)
    return packed


def _scan(text: str) -> list[_Line]:
    """Return each line of `text` with its heading level, 0 for none, and its fence state."""
    lines = []
    fence = ""
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        opener = _FENCE.match(raw)
        if fence:
            marker = opener.group(1) if opener is not None else ""
            if marker[:1] == fence[0] and len(marker) >= len(fence) and not raw.strip(" `~"):
                fence = ""
            lines.append(_Line(raw, 0, True))
            continue
        if opener is not None:
            fence = opener.group(1)
            lines.append(_Line(raw, 0, True))
            continue
        heading = _HEADING.match(raw)
        lines.append(_Line(raw, len(heading.group(1)) if heading else 0, False))
    return lines


def _sections(lines: list[_Line]) -> list[tuple[tuple[str, ...], list[_Line]]]:
    """Return the sections of `lines`, each with the heading path of its heading."""
    sections: list[tuple[tuple[str, ...], list[_Line]]] = [((), [])]
    stack: list[tuple[int, str]] = []
    for line in lines:
        if line.level:
            while stack and stack[-1][0] >= line.level:
                stack.pop()
            stack.append((line.level, line.text.strip()))
            sections.append((tuple(text for _, text in stack), []))
        sections[-1][1].append(line)
    return sections


def _blocks(lines: list[_Line]) -> list[list[str]]:
    """Return the runs of lines between blank lines outside fences."""
    blocks: list[list[str]] = [[]]
    for line in lines:
        if not line.fenced and not line.text.strip():
            if blocks[-1]:
                blocks.append([])
            continue
        blocks[-1].append(line.text)
    return [block for block in blocks if block]


def _cut_block(block: list[str], chunk_chars: int) -> list[str]:
    """Return `block` cut at line ends into pieces of at most `chunk_chars` characters.

    A line longer than `chunk_chars` is cut at that size. A pipe table repeats its first
    two rows at the top of each piece after the first, when they leave room for a row.
    """
    header: list[str] = []
    if len(block) > 2 and block[0].lstrip().startswith("|") and _TABLE_DELIMITER.match(block[1]):
        header = block[:2]
        if len("\n".join(header)) * 2 >= chunk_chars:
            header = []
    pieces: list[str] = []
    current: list[str] = []
    for line in block:
        for part in _cut_line(line, chunk_chars):
            if current and len("\n".join([*current, part])) > chunk_chars:
                pieces.append("\n".join(current))
                current = list(header) if header and part.lstrip().startswith("|") else []
                if current and len("\n".join([*current, part])) > chunk_chars:
                    current = []
            current.append(part)
    if current:
        pieces.append("\n".join(current))
    return pieces


def _cut_line(line: str, chunk_chars: int) -> list[str]:
    """Return `line` cut into parts of at most `chunk_chars` characters."""
    if len(line) <= chunk_chars:
        return [line]
    return [line[start : start + chunk_chars] for start in range(0, len(line), chunk_chars)]


def _join(lines: list[_Line]) -> str:
    """Return the text of `lines` without the blank lines at either end."""
    texts = [line.text for line in lines]
    while texts and not texts[0].strip():
        texts.pop(0)
    while texts and not texts[-1].strip():
        texts.pop()
    return "\n".join(texts)
