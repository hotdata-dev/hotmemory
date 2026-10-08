"""Close the current revision of a key and write a fact as its next revision."""

from _store import add_fact_flags, fact, memory, parser, scope

args_parser = parser(__doc__)
args_parser.add_argument("--scope", required=True, type=scope)
args_parser.add_argument("--key", required=True)
args_parser.add_argument("--actor", default="")
add_fact_flags(args_parser)
args = args_parser.parse_args()
with memory(args) as agent_memory:
    print(agent_memory.supersede(args.scope, args.key, fact(args), actor=args.actor))
