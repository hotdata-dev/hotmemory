"""The fused ranking of `HotdataStore` with a chosen set of its arms, for the ablation.

Each ablated ranking builds the query of `hotmemory.hotdata._fused_query` with only the
named arms: `bm25`, `content`, and `cue`. With every arm, the query is the same as that of
`ranking="fused"`.
"""

from __future__ import annotations

import builtins
from collections.abc import Sequence

from hotmemory import Filter, Hit
from hotmemory.hotdata import (
    CUE_TABLE,
    READ_COLUMNS,
    RRF_CONSTANT,
    HotdataStore,
    _bm25_text,
    _depth,
    _record,
    _ref,
    _tables,
    _text,
    _union,
    _vector,
)

ARMS = ("bm25", "content", "cue")
ABLATIONS = {
    "bm25-content": ("bm25", "content"),
    "content-cue": ("content", "cue"),
    "bm25": ("bm25",),
}


class ArmStore(HotdataStore):
    """A `HotdataStore` whose search fuses only the arms in `arms`."""

    def __init__(self, *args: object, arms: Sequence[str], **kwargs: object) -> None:
        if not arms or set(arms) - set(ARMS):
            raise ValueError(f"arms must be a non-empty subset of {ARMS}, got {arms!r}")
        super().__init__(*args, ranking="fused", **kwargs)  # type: ignore[arg-type]
        self._arms = tuple(arms)

    def search(
        self,
        query: str | None,
        prefixes: Sequence[Sequence[str]],
        filter: Filter | None = None,
        k: int = 10,
    ) -> builtins.list[Hit]:
        if query is None or k == 0:
            return super().search(query, prefixes, filter, k)
        where = self._listed(prefixes, filter)
        tables = _tables(filter)
        vector = _vector(self._embed([query])[0])
        sql = arm_query(tables, where, vector, _bm25_text(query), k, self._arms)
        return [Hit(_record(row), float(row["distance"])) for row in self._sql(sql).to_records()]


def arm_query(
    tables: Sequence[str], where: str, vector: str, text: str, k: int, arms: Sequence[str]
) -> str:
    """Return the fused query of `hotmemory.hotdata._fused_query` with only `arms`."""
    depth = _depth(k)
    rank = "ROW_NUMBER() OVER (ORDER BY {order}) AS rank"
    parts = []
    rankings = []
    if "bm25" in arms:
        for table in tables:
            name = f"text_{table}"
            parts.append(
                f"{name} AS (SELECT id, {rank.format(order='score DESC, id ASC')} FROM "
                f"bm25_search('{_ref(table)}', 'content', {_text(text)}, {depth}) WHERE {where})"
            )
            rankings.append(name)
    if "content" in arms:
        nearest = " UNION ALL ".join(
            f"SELECT scanned.id, cosine_distance(scanned.content_embedding, {vector}) AS distance "
            f"FROM {_ref(table)} scanned WHERE {where}"
            for table in tables
        )
        parts.append(
            f"content_rank AS (SELECT candidate.id, "
            f"{rank.format(order='candidate.distance ASC, candidate.id ASC')} FROM ("
            f"SELECT * FROM ({nearest}) ORDER BY distance ASC, id ASC LIMIT {depth}) candidate)"
        )
        rankings.append("content_rank")
    if "cue" in arms:
        parts.append(
            f"cue_rank AS (SELECT candidate.id, "
            f"{rank.format(order='candidate.distance ASC, candidate.id ASC')} FROM ("
            f"SELECT scanned.id, cosine_distance(scanned.cues_embedding, {vector}) AS distance "
            f"FROM {_ref(CUE_TABLE)} scanned WHERE scanned.id IN ({_union(tables, 'id', where)}) "
            f"ORDER BY distance ASC, id ASC LIMIT {depth}) candidate)"
        )
        rankings.append("cue_rank")
    ranked = " UNION ALL ".join(
        f"SELECT id, rank FROM {name} WHERE rank <= {depth}" for name in rankings
    )
    parts.append(
        f"fused AS (SELECT id, sum(1.0 / ({RRF_CONSTANT} + rank)) AS score "
        f"FROM ({ranked}) GROUP BY id)"
    )
    columns = ", ".join(READ_COLUMNS)
    rows = _union(tables, f"{columns}, content_embedding", where)
    return (
        f"WITH {', '.join(parts)} "
        f"SELECT {', '.join(f'r.{column}' for column in READ_COLUMNS)}, "
        f"cosine_distance(r.content_embedding, {vector}) AS distance FROM fused "
        f"JOIN ({rows}) r ON r.id = fused.id "
        f"ORDER BY fused.score DESC, distance ASC, r.created_at DESC, r.id ASC LIMIT {k}"
    )
