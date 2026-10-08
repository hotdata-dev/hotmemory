"""Print the records near a fact under the scopes, closest first, with their distances."""

from _store import add_fact_flags, fact, memory, parser, scope

args_parser = parser(__doc__)
args_parser.add_argument("--scope", required=True, action="append", type=scope, dest="scopes")
args_parser.add_argument("--k", type=int, default=5)
add_fact_flags(args_parser)
args = args_parser.parse_args()
with memory(args) as agent_memory:
    for hit in agent_memory.candidates(fact(args), args.scopes, args.k):
        print(f"{hit.distance:.4f} {hit.record.id} {hit.record.content}")
