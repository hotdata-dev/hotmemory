import re
import zlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from hotmemory import Clock, Embedder, MemoryStore, Store

START = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
DIMENSIONS = 64


class FakeClock:
    """A clock that stands still until a test moves it."""

    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta = timedelta(seconds=1)) -> None:
        self.now += delta


def fake_embedder(texts: Sequence[str]) -> list[list[float]]:
    """Embed each text as counts of its words, hashed into a fixed number of dimensions."""
    vectors = []
    for text in texts:
        vector = [0.0] * DIMENSIONS
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[zlib.crc32(word.encode()) % DIMENSIONS] += 1.0
        vectors.append(vector)
    return vectors


StoreFactory = Callable[[Embedder | None, Clock], Store]

DRIVERS: dict[str, StoreFactory] = {
    "memory": lambda embedder, clock: MemoryStore(embedder=embedder, clock=clock),
}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(params=sorted(DRIVERS))
def store(request: pytest.FixtureRequest, clock: FakeClock) -> Store:
    factory: StoreFactory = DRIVERS[request.param]
    return factory(fake_embedder, clock)
