"""Run an extractor over a text, remember the facts it returns, and print their ids.

With --episode, the text is also stored as an episode, and each fact names it in its sources.
With --chunk-chars as well, the text is stored as chunks of at most that many characters.
"""

import importlib

from _store import memory, parse_time, parser, scope

args_parser = parser(__doc__)
args_parser.add_argument("--text", required=True)
args_parser.add_argument("--scope", required=True, type=scope)
args_parser.add_argument(
    "--extractor", required=True, help="MODULE:FUNCTION, importable from this folder"
)
args_parser.add_argument("--actor", default="")
args_parser.add_argument("--observed-at", type=parse_time)
args_parser.add_argument("--episode", help="a key: also store the text as an episode under it")
args_parser.add_argument(
    "--chunk-chars", type=int, help="with --episode: store the text as chunks of this size"
)
args = args_parser.parse_args()
module, _, name = args.extractor.partition(":")
extractor = getattr(importlib.import_module(module), name)
with memory(args) as agent_memory:
    ids = agent_memory.capture(
        args.text,
        args.scope,
        extractor,
        args.actor,
        args.observed_at,
        args.episode,
        args.chunk_chars,
    )
    print(*ids, sep="\n")
