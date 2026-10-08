"""Remember one fact under a scope, and print its id."""

from _store import add_fact_flags, fact, memory, parser, scope

args_parser = parser(__doc__)
args_parser.add_argument("--scope", required=True, type=scope)
args_parser.add_argument("--actor", default="")
add_fact_flags(args_parser)
args = args_parser.parse_args()
with memory(args) as agent_memory:
    print(*agent_memory.remember([fact(args)], args.scope, args.actor), sep="\n")
