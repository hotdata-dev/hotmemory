# Roadmap

Every phase of the work, one paragraph each, with its status. This file changes when a
phase opens or closes, and not in between. The detail of the open phase lives in
`plan.md`. The design and its reasons live in `brief.md`.

Rules. One phase is open at a time. A phase closes when every issue it opened is closed, or
is listed under Deferred below with a reason. The next phase opens only then. Each phase is
one pull request and ends with the verification layer that proves it.

## Phases

| Phase | Delivers | Status |
|---|---|---|
| 0. Documents and measurements | The public contracts and guarantees files, CONTRIBUTING, the Makefile with the first documentation check, the local container setup, and measurements M1 to M6. | open |
| 1. The storage contract, offline | The record, the `Store` protocol, the in-memory driver, the conformance suite, the frozen surfaces, the ledger test, and CI. | planned |
| 2. The Hotdata driver | `HotdataStore` with two tables, the serialized writer, the three-stage retrieval query, the sweeper, the integration leg, and the oracle test. | planned |
| 3. The memory contract | `Memory` with remember, recall, candidates, supersede, forget, profile, and capture, the rendered blocks pinned by tests, and the skill file with its command check. | planned |
| 4. The first consumer | A post-mortem loader and a recall at investigation start, in the consumer's own repository, proven by a replay case. | planned |
| 5. A public benchmark | A LongMemEval-S runner measuring retrieval recall at k first and end-to-end accuracy second, with the numbers in `docs/benchmarks.md`. | planned |
| 6. Adapters | A LangGraph `BaseStore` adapter in `hotdata-langchain`, then an MCP server. | planned, out of scope for version 1 |

## Deferred

Nothing yet. An entry here names the issue, the phase it came from, and the reason it is
not being taken up now.

## Closed phases

None yet. A closed phase keeps its row above with the date it closed and the pull request.
