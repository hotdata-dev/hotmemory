"""Recall the records for a query under the scopes, and print the block."""

from _store import memory, parse_time, parser, scope

args_parser = parser(__doc__)
args_parser.add_argument("--query", required=True)
args_parser.add_argument("--scope", required=True, action="append", type=scope, dest="scopes")
args_parser.add_argument("--as-of", type=parse_time)
args_parser.add_argument("--budget", type=int, default=2000)
args_parser.add_argument("--k", type=int, default=10)
args = args_parser.parse_args()
with memory(args) as agent_memory:
    _, block = agent_memory.recall(args.query, args.scopes, args.as_of, args.budget, args.k)
    print(block)
