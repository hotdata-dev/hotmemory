"""`OpenAIEmbedder`: an `Embedder` over the OpenAI embeddings API.

Needs the `openai` extra: `pip install hotmemory[openai]`.
"""

from __future__ import annotations

from collections.abc import Sequence

from openai import OpenAI

DEFAULT_MODEL = "text-embedding-3-small"
MODEL_DIMENSIONS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}
MAX_BATCH = 2048


class OpenAIEmbedder:
    """An `Embedder` that sends texts to the OpenAI embeddings API.

    `model` names the embedding model. `dimensions`, if given, asks the API for vectors of
    that size, which only the `text-embedding-3` models accept. `client` defaults to
    `OpenAI()`, which reads `OPENAI_API_KEY`. A call sends at most `batch_size` texts per
    request and returns one vector for each text, in the order of the texts.

    `self.dimensions` is the size of the vectors that the embedder returns, or None if
    the model is not one of `MODEL_DIMENSIONS` and no size was given. Pass `self.model`
    and `self.dimensions` to `HotdataStore.provision`.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        dimensions: int | None = None,
        client: OpenAI | None = None,
        batch_size: int = MAX_BATCH,
    ) -> None:
        if not 1 <= batch_size <= MAX_BATCH:
            raise ValueError(f"batch_size must be from 1 to {MAX_BATCH}, got {batch_size}")
        if dimensions is not None and dimensions < 1:
            raise ValueError("dimensions must be 1 or more")
        self.model = model
        self.dimensions = MODEL_DIMENSIONS.get(model) if dimensions is None else dimensions
        self._requested = dimensions
        self._client = OpenAI() if client is None else client
        self._batch_size = batch_size

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        if isinstance(texts, str):
            raise TypeError("texts must be a sequence of strings, not a string")
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            if self._requested is None:
                response = self._client.embeddings.create(model=self.model, input=batch)
            else:
                response = self._client.embeddings.create(
                    model=self.model, input=batch, dimensions=self._requested
                )
            ordered = sorted(response.data, key=lambda item: item.index)
            if len(ordered) != len(batch):
                raise ValueError(f"OpenAI returned {len(ordered)} vectors for {len(batch)} texts")
            vectors.extend(list(item.embedding) for item in ordered)
        return vectors
