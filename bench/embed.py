"""The embedder of the runs: OpenAI embeddings through a cache on disk."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from array import array
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from hotmemory import Embedder

CACHE_DIR = Path(__file__).resolve().parent / "cache"
MODEL = "text-embedding-3-small"
DIMENSIONS = 1536
ENCODING = "cl100k_base"
MAX_INPUT_TOKENS = 8191
MAX_REQUEST_TOKENS = 250_000


class Tokens(Protocol):
    """Counts the tokens of a text, and cuts a text to a number of tokens."""

    def count(self, text: str) -> int: ...

    def truncate(self, text: str, limit: int) -> str: ...


class TiktokenTokens:
    """`Tokens` with a tiktoken encoding. The encoding files are kept in the cache folder."""

    def __init__(self, encoding: str = ENCODING, cache_dir: Path = CACHE_DIR) -> None:
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(cache_dir / "tiktoken"))
        import tiktoken

        self._encoding = tiktoken.get_encoding(encoding)

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text, disallowed_special=()))

    def truncate(self, text: str, limit: int) -> str:
        tokens = self._encoding.encode(text, disallowed_special=())
        return text if len(tokens) <= limit else self._encoding.decode(tokens[:limit])


class CachedEmbedder:
    """An `Embedder` that keeps each vector in a SQLite file and calls `inner` for the rest.

    Each text is cut to `max_input` tokens before it is embedded. The cache key is the
    SHA-256 of `model` and the cut text, so a second run with the same texts calls no
    model. The texts that miss the cache go to `inner` in batches of at most
    `max_request` tokens. The vectors are stored as 32-bit floats. `calls` counts the
    calls to `inner`, and `misses` counts the texts sent to it.
    """

    def __init__(
        self,
        inner: Embedder,
        model: str,
        path: Path,
        tokens: Tokens,
        *,
        max_input: int = MAX_INPUT_TOKENS,
        max_request: int = MAX_REQUEST_TOKENS,
    ) -> None:
        self.model = model
        self.calls = 0
        self.misses = 0
        self._inner = inner
        self._tokens = tokens
        self._max_input = max_input
        self._max_request = max_request
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=60)
        self._db.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB)")

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        if isinstance(texts, str):
            raise TypeError("texts must be a sequence of strings, not a string")
        cut = [self._tokens.truncate(text, self._max_input) for text in texts]
        keys = [self._key(text) for text in cut]
        found = self._read(keys)
        missing = list(dict.fromkeys(key for key in keys if key not in found))
        texts_of = dict(zip(keys, cut, strict=True))
        batch: list[str] = []
        used = 0
        for key in missing:
            size = self._tokens.count(texts_of[key])
            if batch and used + size > self._max_request:
                found.update(self._embed(batch, texts_of))
                batch, used = [], 0
            batch.append(key)
            used += size
        if batch:
            found.update(self._embed(batch, texts_of))
        return [list(found[key]) for key in keys]

    def close(self) -> None:
        """Close the cache file."""
        self._db.close()

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\n{text}".encode()).hexdigest()

    def _read(self, keys: Sequence[str]) -> dict[str, list[float]]:
        found: dict[str, list[float]] = {}
        unique = list(dict.fromkeys(keys))
        for start in range(0, len(unique), 500):
            part = unique[start : start + 500]
            marks = ", ".join("?" * len(part))
            rows = self._db.execute(f"SELECT key, vector FROM vectors WHERE key IN ({marks})", part)
            for key, blob in rows:
                found[key] = array("f", blob).tolist()
        return found

    def _embed(self, keys: Sequence[str], texts_of: dict[str, str]) -> dict[str, list[float]]:
        vectors = self._inner([texts_of[key] for key in keys])
        if len(vectors) != len(keys):
            raise ValueError(f"embedder returned {len(vectors)} vectors for {len(keys)} texts")
        self.calls += 1
        self.misses += len(keys)
        stored = {key: array("f", vector) for key, vector in zip(keys, vectors, strict=True)}
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO vectors (key, vector) VALUES (?, ?)",
                [(key, vector.tobytes()) for key, vector in stored.items()],
            )
        return {key: vector.tolist() for key, vector in stored.items()}


def openai_embedder(cache_dir: Path = CACHE_DIR) -> CachedEmbedder:
    """Return `CachedEmbedder` over `OpenAIEmbedder` with MODEL, cached in `cache_dir`.

    The OpenAI client reads `OPENAI_API_KEY`.
    """
    from hotmemory.openai import OpenAIEmbedder

    return CachedEmbedder(
        OpenAIEmbedder(MODEL),
        MODEL,
        cache_dir / "embeddings.sqlite",
        TiktokenTokens(cache_dir=cache_dir),
    )
