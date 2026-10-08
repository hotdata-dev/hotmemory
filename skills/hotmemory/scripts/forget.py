"""Delete the keys of the named ids, or the keys due before a horizon, and print the ids."""

from _store import memory, parse_time, parser, scope

args_parser = parser(__doc__)
args_parser.add_argument("--scope", required=True, action="append", type=scope, dest="scopes")
which = args_parser.add_mutually_exclusive_group(required=True)
which.add_argument("--id", action="append", dest="ids")
which.add_argument("--horizon", type=parse_time)
args = args_parser.parse_args()
with memory(args) as agent_memory:
    deleted = agent_memory.forget(args.scopes, ids=args.ids, horizon=args.horizon)
    print(*deleted, sep="\n")
