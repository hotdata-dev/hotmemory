"""Load a Markdown document: store its chunks as episodes, extract facts, and print the ids."""

import importlib

from _store import memory, parse_time, parser, scope

args_parser = parser(__doc__)
args_parser.add_argument("--document", required=True, help="the name of the document")
args_parser.add_argument("--file", required=True, help="the Markdown file to load")
args_parser.add_argument("--scope", required=True, type=scope)
args_parser.add_argument(
    "--extractor", required=True, help="MODULE:FUNCTION, importable from this folder"
)
args_parser.add_argument("--actor", default="")
args_parser.add_argument("--observed-at", type=parse_time)
args_parser.add_argument("--chunk-chars", type=int, default=2000)
args = args_parser.parse_args()
module, _, name = args.extractor.partition(":")
extractor = getattr(importlib.import_module(module), name)
with open(args.file, encoding="utf-8") as source:
    text = source.read()
with memory(args) as agent_memory:
    episodes, facts = agent_memory.load(
        args.document,
        text,
        args.scope,
        extractor,
        args.actor,
        args.observed_at,
        args.chunk_chars,
    )
    print(*episodes, *facts, sep="\n")
