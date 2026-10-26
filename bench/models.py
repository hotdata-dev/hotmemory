"""The model calls of the end-to-end run, through a cache on disk, and their prices."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

READER = "gpt-4o-2024-08-06"
JUDGE = "gpt-4o-2024-08-06"
EXTRACTOR = "gpt-4o-mini-2024-07-18"
READER_MAX_TOKENS = 800
JUDGE_MAX_TOKENS = 10
EXTRACTOR_MAX_TOKENS = 4000
BEAM_MINI = "gpt-4.1-mini-2025-04-14"
PRICES = {
    READER: (2.50, 10.00),
    EXTRACTOR: (0.15, 0.60),
    BEAM_MINI: (0.40, 1.60),
}
"""US dollars for one million input tokens and one million output tokens.

The first two were read on 2026-10-10, and `gpt-4.1-mini` on 2026-10-26.
"""
EMBEDDING_PRICE = 0.02
"""US dollars for one million tokens of `text-embedding-3-small`, read on 2026-10-26."""


@dataclass(frozen=True)
class Reply:
    """The text of one model reply and its token counts.

    `stopped` is True if a reply with a schema reached its token limit, so its text is not
    whole JSON.
    """

    text: str
    input_tokens: int
    output_tokens: int
    stopped: bool = False

    def cost(self, model: str) -> float:
        """Return the price of this reply in US dollars."""
        price_in, price_out = PRICES[model]
        return (self.input_tokens * price_in + self.output_tokens * price_out) / 1_000_000


class Complete(Protocol):
    """Sends one prompt to one model, after `system` as a system message if given, and returns
    the reply."""

    def __call__(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        schema: dict[str, Any] | None,
        system: str | None = None,
    ) -> Reply: ...


class CachedModels:
    """Calls `complete` for each prompt that the cache does not hold, and keeps the reply.

    The cache key is the SHA-256 of the model, the token limit, the schema, and the prompt,
    and of the system message if there is one.
    `spent` sums the price of the replies that came from a model call in this process, and
    `calls` counts those calls.
    """

    def __init__(self, complete: Complete, path: Path) -> None:
        self.spent = 0.0
        self.calls = 0
        self._complete = complete
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=60, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS replies (key TEXT PRIMARY KEY, reply TEXT)")

    def __call__(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        schema: dict[str, Any] | None = None,
        system: str | None = None,
    ) -> Reply:
        parts: list[Any] = [model, max_tokens, schema, prompt]
        if system is not None:
            parts.append(system)
        key = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
        row = self._db.execute("SELECT reply FROM replies WHERE key = ?", (key,)).fetchone()
        if row is not None:
            return Reply(**json.loads(row[0]))
        reply = self._complete(model, prompt, max_tokens, schema, system)
        self.calls += 1
        self.spent += reply.cost(model)
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO replies (key, reply) VALUES (?, ?)",
                (key, json.dumps(reply.__dict__)),
            )
        return reply

    def close(self) -> None:
        """Close the cache file."""
        self._db.close()


class Offline:
    """`Complete` that calls no model: it raises `CacheMiss` for each prompt.

    Wrapped in `CachedModels`, it replays the cached replies and stops at the first prompt
    that the cache does not hold.
    """

    def __call__(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        schema: dict[str, Any] | None,
        system: str | None = None,
    ) -> Reply:
        raise CacheMiss(f"the cache holds no reply of {model} to this prompt: {prompt[:80]!r}")


class CacheMiss(RuntimeError):
    """A replay needed a reply that the cache does not hold."""


class Remote:
    """`Complete` over the OpenAI chat API, at temperature 0, with 120 seconds for each try.

    The client reads the key from `OPENAI_MODEL_KEY`. `OPENAI_API_KEY` stays the key of the
    embedder. With a schema, the reply is JSON that the schema allows, or a reply with
    `stopped` set if it reached `max_tokens`.
    """

    def __init__(self) -> None:
        import openai

        self._openai = openai.OpenAI(
            api_key=os.environ["OPENAI_MODEL_KEY"], max_retries=6, timeout=120.0
        )

    def __call__(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        schema: dict[str, Any] | None,
        system: str | None = None,
    ) -> Reply:
        messages: list[Any] = [{"role": "user", "content": prompt}]
        if system is not None:
            messages.insert(0, {"role": "system", "content": system})
        extra: dict[str, Any] = {}
        if schema is not None:
            extra["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "strict": True, "schema": schema},
            }
        completion = self._openai.chat.completions.create(
            model=model,
            messages=messages,
            n=1,
            temperature=0,
            max_tokens=max_tokens,
            **extra,
        )
        choice = completion.choices[0]
        usage = completion.usage
        return Reply(
            text=(choice.message.content or "").strip(),
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            stopped=choice.finish_reason == "length" and schema is not None,
        )
