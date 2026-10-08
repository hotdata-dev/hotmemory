"""Offline tests of `OpenAIEmbedder`, with a fake client and no network."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from hotmemory.openai import OpenAIEmbedder


@dataclass
class Item:
    index: int
    embedding: list[float]


@dataclass
class Response:
    data: list[Item]


@dataclass
class FakeEmbeddings:
    """Answers each text with [its length, its index in the request], in reverse order."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> Response:
        self.calls.append(kwargs)
        items = [Item(i, [float(len(text)), float(i)]) for i, text in enumerate(kwargs["input"])]
        return Response(list(reversed(items)))


@dataclass
class FakeClient:
    embeddings: FakeEmbeddings = field(default_factory=FakeEmbeddings)


def embedder(client: FakeClient, **kwargs: Any) -> OpenAIEmbedder:
    return OpenAIEmbedder(client=client, **kwargs)  # type: ignore[arg-type]


def test_vectors_come_back_in_the_order_of_the_texts() -> None:
    client = FakeClient()
    vectors = embedder(client)(["a", "bb", "ccc"])

    assert vectors == [[1.0, 0.0], [2.0, 1.0], [3.0, 2.0]]
    assert client.embeddings.calls == [
        {"model": "text-embedding-3-small", "input": ["a", "bb", "ccc"]}
    ]


def test_texts_go_in_batches() -> None:
    client = FakeClient()
    vectors = embedder(client, batch_size=2)(["a", "bb", "ccc", "dddd", "e"])

    assert [vector[0] for vector in vectors] == [1.0, 2.0, 3.0, 4.0, 1.0]
    assert [call["input"] for call in client.embeddings.calls] == [
        ["a", "bb"],
        ["ccc", "dddd"],
        ["e"],
    ]


def test_dimensions_are_sent_only_when_given() -> None:
    client = FakeClient()
    sized = embedder(client, model="text-embedding-3-large", dimensions=256)
    sized(["a"])

    assert client.embeddings.calls[0]["dimensions"] == 256
    assert (sized.model, sized.dimensions) == ("text-embedding-3-large", 256)
    assert embedder(FakeClient()).dimensions == 1536
    assert embedder(FakeClient(), model="another").dimensions is None


def test_no_texts_send_no_request() -> None:
    client = FakeClient()
    assert embedder(client)([]) == []
    assert client.embeddings.calls == []


@pytest.mark.parametrize("arguments", [{"batch_size": 0}, {"batch_size": 2049}, {"dimensions": 0}])
def test_bad_settings_raise(arguments: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        embedder(FakeClient(), **arguments)


def test_a_string_is_not_a_list_of_texts() -> None:
    with pytest.raises(TypeError):
        embedder(FakeClient())("one text")
