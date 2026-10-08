"""Print the profile block of a subject under the scopes."""

from _store import memory, parser, scope

args_parser = parser(__doc__)
args_parser.add_argument("--subject", required=True)
args_parser.add_argument("--scope", required=True, action="append", type=scope, dest="scopes")
args_parser.add_argument("--budget", type=int, default=2000)
args = args_parser.parse_args()
with memory(args) as agent_memory:
    _, block = agent_memory.profile(args.subject, args.scopes, args.budget)
    print(block)
