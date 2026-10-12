"""The episodes of a question: one episode for each session, with the user turns as content."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from bench.data import Question
from hotmemory import Store

ACTOR = "longmemeval"


def scope(run: str, question: Question) -> tuple[str, ...]:
    """Return the namespace of `question` in the run named `run`."""
    return ("bench", "lme_s", run, question.id)


def write_episodes(store: Store, namespace: Sequence[str], question: Question) -> list[str]:
    """Write one episode for each session of `question`, in date order, in one flush.

    The key is the session key, the content is the user text, the source is the session
    source, and `observed_at` is the session date. Returns the ids that the flush wrote.
    """
    sessions = question.episodes()
    with store.writer(max_rows=len(sessions) + 1, interval=timedelta.max) as writer:
        for session in sessions:
            writer.put(
                namespace,
                session.key,
                kind="episode",
                content=session.user_text,
                sources=(session.source,),
                actor=ACTOR,
                observed_at=session.date,
            )
    return writer.flushed


def session_of(sources: Sequence[str]) -> str:
    """Return the session id in `sources`, or raise ValueError if there is none."""
    for source in sources:
        if source.startswith("session:"):
            return source.removeprefix("session:")
    raise ValueError(f"no session source in {list(sources)!r}")
