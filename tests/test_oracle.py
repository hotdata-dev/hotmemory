"""The oracle: `MemoryStore` checks the SQL that `HotdataStore` sends, on the same records."""

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from hotdata_framework import HotdataClient, ManagedDatabase

from hotmemory import Fact, Filter, Memory, MemoryStore, Record, Store
from hotmemory.hotdata import HotdataStore, Ranking

from .conftest import DIMENSIONS, MODEL, START, FakeClock, empty, fake_embedder

pytestmark = pytest.mark.hotdata

TOLERANCE = 1e-6

PAY = ("team", "payments")
PLATFORM = ("team", "platform")
SEARCH = ("team", "search")
PUTS: list[tuple[tuple[str, ...], str, dict[str, Any]]] = [
    (PAY, "disk", {"kind": "fact", "content": "The disk on db-1 fills at night."}),
    (PAY, "disk", {"kind": "fact", "content": "The disk on db-1 fills at noon."}),
    (PAY, "pool", {"kind": "fact", "content": "The connection pool runs dry."}),
    (
        PAY,
        "cert",
        {"kind": "procedure", "content": "Renew the certificate first.", "tags": ("tls",)},
    ),
    (
        (*PAY, "eu"),
        "latency",
        {"kind": "fact", "content": "Checkout latency rises after a deploy.", "tags": ("deploy",)},
    ),
    (
        PLATFORM,
        "dns",
        {"kind": "fact", "content": "The resolver times out.", "cues": ("why dns fails",)},
    ),
    (PLATFORM, "queue", {"kind": "profile", "content": "The queue owner is the platform team."}),
    (
        PLATFORM,
        "thread",
        {"kind": "episode", "content": "Incident thread: the disk filled and the queue backed up."},
    ),
    (
        PLATFORM,
        "flag",
        {"kind": "fact", "content": "The feature flag hides the bug.", "forget_after": "past"},
    ),
    (SEARCH, "shard", {"kind": "fact", "content": "A replica shard lags behind the leader."}),
    (
        SEARCH,
        "index",
        {"kind": "fact", "content": "The disk index rebuild takes an hour.", "tags": ("disk",)},
    ),
]

QUERIES: list[tuple[str, list[tuple[str, ...]], Filter | None]] = [
    ("disk fills", [("team",)], None),
    ("the disk", [("team", "payments"), ("team", "search")], None),
    ("deploy latency", [("team", "payments")], None),
    ("queue backed up", [("team",)], Filter(kind="episode")),
    ("certificate", [("team",)], Filter(kind="procedure", tags=("tls",))),
    ("resolver load", [("team", "platform")], Filter(kind="fact")),
    ("nothing matches this", [("team",)], None),
]


def fill(store: Store, clock: FakeClock) -> None:
    for namespace, key, fields in PUTS:
        if fields.get("forget_after") == "past":
            fields = {**fields, "forget_after": clock.now + timedelta(minutes=1)}
        store.put(namespace, key, **fields)
        clock.advance()
    clock.advance(timedelta(hours=1))


def hotdata_store(
    client: HotdataClient, database: ManagedDatabase, clock: FakeClock, ranking: Ranking
) -> HotdataStore:
    return HotdataStore(
        client,
        database,
        embedder=fake_embedder,
        model=MODEL,
        dimensions=DIMENSIONS,
        clock=clock,
        ranking=ranking,
    )


@pytest.fixture
def pair(
    hotdata_database: tuple[HotdataClient, ManagedDatabase], request: pytest.FixtureRequest
) -> Iterator[tuple[MemoryStore, dict[Ranking, HotdataStore]]]:
    client, database = hotdata_database
    clock = FakeClock()
    oracle = MemoryStore(embedder=fake_embedder, clock=clock)
    drivers: dict[Ranking, HotdataStore] = {
        "vector": hotdata_store(client, database, clock, "vector"),
        "fused": hotdata_store(client, database, clock, "fused"),
    }
    fill(oracle, clock)
    end = clock.now
    clock.now = START
    fill(drivers["vector"], clock)
    assert clock.now == end
    yield oracle, drivers
    empty(drivers["vector"])


@pytest.mark.parametrize(("query", "prefixes", "where"), QUERIES, ids=[q[0] for q in QUERIES])
def test_vector_ranking_matches_the_oracle(
    pair: tuple[MemoryStore, dict[Ranking, HotdataStore]],
    query: str,
    prefixes: list[tuple[str, ...]],
    where: Filter | None,
) -> None:
    oracle, drivers = pair
    expected = oracle.search(query, prefixes, where, k=5)
    found = drivers["vector"].search(query, prefixes, where, k=5)

    assert [hit.record.id for hit in found] == [hit.record.id for hit in expected]
    for hit, wanted in zip(found, expected, strict=True):
        assert hit.distance is not None and wanted.distance is not None
        assert abs(hit.distance - wanted.distance) <= TOLERANCE
        assert hit.record == wanted.record


@pytest.mark.parametrize(("query", "prefixes", "where"), QUERIES, ids=[q[0] for q in QUERIES])
def test_fused_ranking_returns_the_oracle_set(
    pair: tuple[MemoryStore, dict[Ranking, HotdataStore]],
    query: str,
    prefixes: list[tuple[str, ...]],
    where: Filter | None,
) -> None:
    oracle, drivers = pair
    k = len(PUTS)
    expected = {hit.record.id for hit in oracle.search(query, prefixes, where, k=k)}
    found = {hit.record.id for hit in drivers["fused"].search(query, prefixes, where, k=k)}

    assert found == expected


def test_the_default_ranking_is_the_vector_ranking(
    hotdata_database: tuple[HotdataClient, ManagedDatabase],
) -> None:
    client, database = hotdata_database
    store = HotdataStore(
        client, database, embedder=fake_embedder, model=MODEL, dimensions=DIMENSIONS
    )
    assert store._ranking == "vector"


SESSIONS = [
    "user: The disk on db-1 fills at night.\n\nassistant: Rotate the logs before noon.",
    "user: The pool ran out of nodes.\n\nassistant: Raise the node group ceiling.\n\n"
    "user: The ceiling is now 8.",
]


def every_turn(text: str, observed_at: object, current: list[Record]) -> list[Fact]:
    """Return one fact for each user turn of a chat."""
    return [
        Fact(kind="fact", subject="chat", content=turn.removeprefix("user: "))
        for turn in text.split("\n\n")
        if turn.startswith("user: ")
    ]


@pytest.mark.parametrize("episodes", ["search", "cited"])
def test_both_drivers_recall_the_same_block_with_chunks(
    hotdata_database: tuple[HotdataClient, ManagedDatabase], episodes: str
) -> None:
    client, database = hotdata_database
    clock = FakeClock()
    oracle = Memory(MemoryStore(embedder=fake_embedder, clock=clock), clock=clock)
    store = HotdataStore(
        client, database, embedder=fake_embedder, model=MODEL, dimensions=DIMENSIONS, clock=clock
    )
    driver = Memory(store, clock=clock)
    try:
        for memory in (oracle, driver):
            clock.now = START
            for number, text in enumerate(SESSIONS, start=1):
                memory.capture(text, PAY, every_turn, episode=f"chat{number}", chunk_chars=50)
                clock.advance()
        blocks = [
            memory.recall("node ceiling", [PAY], k=3, budget=4000, episodes=episodes)  # type: ignore[arg-type]
            for memory in (oracle, driver)
        ]
        assert blocks[1][1] == blocks[0][1]
        assert [record.id for record in blocks[1][0]] == [record.id for record in blocks[0][0]]
    finally:
        empty(store)


@pytest.mark.parametrize("ranking", ["vector", "fused"])
def test_capture_searches_with_the_ranking_of_recall(
    hotdata_database: tuple[HotdataClient, ManagedDatabase], ranking: Ranking
) -> None:
    client, database = hotdata_database
    clock = FakeClock()
    store = hotdata_store(client, database, clock, ranking)
    memory = Memory(store, clock=clock)
    seen: list[list[str]] = []

    def extractor(text: str, observed_at: object, current: list[Record]) -> list[Fact]:
        seen.append([record.id for record in current])
        return []

    try:
        fill(store, clock)
        text = "The disk on db-1 fills, and the queue backed up."
        expected, _ = memory.recall(text, [("team",)])
        memory.capture(text, ("team",), extractor)
        assert seen == [[record.id for record in expected]]
        assert expected
    finally:
        empty(store)
