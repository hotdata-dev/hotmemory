"""Run an extractor over a text, remember the facts it returns, and print their ids."""

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
args = args_parser.parse_args()
module, _, name = args.extractor.partition(":")
extractor = getattr(importlib.import_module(module), name)
with memory(args) as agent_memory:
    ids = agent_memory.capture(args.text, args.scope, extractor, args.actor, args.observed_at)
    print(*ids, sep="\n")
