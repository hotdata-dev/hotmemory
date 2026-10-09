"""An example extractor for `capture.py`. It calls no model."""

from datetime import datetime

from hotmemory import Fact, Record, normalize


def one_fact_per_sentence(
    text: str, observed_at: datetime | None, current: list[Record]
) -> list[Fact]:
    """Return one fact for each sentence of `text` that no current record holds already."""
    known = {normalize(record.content) for record in current}
    sentences = [f"{part.strip()}." for part in text.split(".") if part.strip()]
    return [Fact(kind="fact", content=s) for s in sentences if normalize(s) not in known]


def one_fact_per_prose_sentence(
    text: str, observed_at: datetime | None, current: list[Record]
) -> list[Fact]:
    """Return one fact for each sentence of a chunk, without its headings and code blocks."""
    prose = []
    fenced = False
    for line in text.split("\n"):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and not line.startswith("#"):
            prose.append(line)
    return one_fact_per_sentence(" ".join(prose), observed_at, current)
