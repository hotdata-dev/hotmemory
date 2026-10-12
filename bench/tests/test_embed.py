from collections.abc import Sequence
from pathlib import Path

from bench.embed import CachedEmbedder
from tests.conftest import fake_embedder


class WordTokens:
    """Counts words as tokens."""

    def count(self, text: str) -> int:
        return len(text.split())

    def truncate(self, text: str, limit: int) -> str:
        return " ".join(text.split()[:limit])


class Recorder:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return fake_embedder(texts)


def test_a_second_call_reads_the_cache(tmp_path: Path) -> None:
    inner = Recorder()
    path = tmp_path / "cache.sqlite"
    embedder = CachedEmbedder(inner, "m", path, WordTokens())
    first = embedder(["red bike", "blue car", "red bike"])
    assert inner.batches == [["red bike", "blue car"]]
    embedder.close()
    again = CachedEmbedder(inner, "m", path, WordTokens())
    assert again(["blue car", "red bike"]) == [first[1], first[0]]
    assert inner.batches == [["red bike", "blue car"]]
    assert (again.calls, again.misses) == (0, 0)


def test_the_key_holds_the_model(tmp_path: Path) -> None:
    inner = Recorder()
    path = tmp_path / "cache.sqlite"
    CachedEmbedder(inner, "m1", path, WordTokens())(["red bike"])
    CachedEmbedder(inner, "m2", path, WordTokens())(["red bike"])
    assert len(inner.batches) == 2


def test_texts_are_cut_and_batched_by_tokens(tmp_path: Path) -> None:
    inner = Recorder()
    embedder = CachedEmbedder(
        inner, "m", tmp_path / "c.sqlite", WordTokens(), max_input=3, max_request=5
    )
    embedder(["a b c d e", "f g", "h i j"])
    assert inner.batches == [["a b c", "f g"], ["h i j"]]
    assert embedder(["a b c x y"]) == embedder(["a b c"])
    assert (embedder.calls, embedder.misses) == (2, 3)


def test_vectors_are_32_bit(tmp_path: Path) -> None:
    def precise(texts: Sequence[str]) -> list[list[float]]:
        return [[0.1, 1 / 3] for _ in texts]

    vector = CachedEmbedder(precise, "m", tmp_path / "c.sqlite", WordTokens())(["x"])[0]
    assert vector != [0.1, 1 / 3]
    assert abs(vector[0] - 0.1) < 1e-7
