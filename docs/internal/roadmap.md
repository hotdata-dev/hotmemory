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
| 0. Documents and measurements | The public contracts and guarantees files, CONTRIBUTING, the Makefile with the first documentation check, the local container setup, and measurements M1 to M6. | closed 2026-10-05, PR #2 |
| 1. The storage contract, offline | The record, the `Store` protocol, the in-memory driver, the conformance suite, the frozen surfaces, the ledger test, and CI. | closed 2026-10-06, PR #5 |
| 2. The Hotdata driver | `HotdataStore` with four tables, the serialized writer, the three-stage retrieval query, the sweeper, the integration leg against the local stack in CI, and the oracle test. | closed 2026-10-08, PR #7 |
| 3. The memory contract | `Memory` with remember, recall, candidates, supersede, forget, profile, and capture, the rendered blocks pinned by tests, and the skill file with its command check. | open |
| 4. The first consumer | A post-mortem loader and a recall at investigation start, in the consumer's own repository, proven by a replay case. | planned |
| 5. A public benchmark | A LongMemEval-S runner measuring retrieval recall at k first and end-to-end accuracy second, with the numbers in `docs/benchmarks.md`. | planned |
| 6. Adapters | A LangGraph `BaseStore` adapter in `hotdata-langchain`, then an MCP server. | planned, out of scope for version 1 |

## Deferred

Nothing yet. An entry here names the issue, the phase it came from, and the reason it is
not being taken up now.

## Closed phases

A closed phase keeps its row above with the date it closed and the pull request.

- Phase 0 closed on 2026-10-05 with PR #2, which closed issue #1. Its measurements are in
  `docs/guarantees.md`. It changed the plan for later phases in three ways. The local
  development target is a three-container stack (Postgres, RustFS, and the engine), not a
  bare container. Fused retrieval needs plain vector indexes and an embedder from the
  caller, because a provider-backed index excludes every other index. The integration leg
  can run against the local stack in CI.
- Phase 1 closed on 2026-10-06 with PR #5, which closed issue #4. It changed the plan for
  later phases in three ways. The record refuses more than the brief said: an empty label,
  a label with `/`, a key with `/` or `@`, and empty content. Deduplication skips a
  current revision past `forget_after`, so a forgotten fact can come back. Two questions
  moved to phase 3: how `supersede` sets fields of the old record, and how a fact gains a
  source when its content does not change.
- Phase 2 closed on 2026-10-08 with PR #7, which closed issue #6. It changed the plan for
  later phases in four ways. The engine allows one vector index per table, so the cue
  vectors have their own table. The engine refuses an index on an empty table, so
  `provision` builds over a seed row. A filtered search through a vector index misses rows
  loaded after the build, so the vector rankings filter after their fetch. The defect is
  reported to the engine team, and it reproduces in production. One process writes to a
  database until the engine has a conditional write.
