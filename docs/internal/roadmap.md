# Roadmap

Every phase of the work, one row each, with its status. Phases 0 to 5 were the first
roadmap, which reached the basic memory layer on 2026-10-12. This file changes when a
phase opens or closes, and not in between. The detail of the open phase lives in
`plan.md`. The design and its reasons live in `brief.md`.

Rules. One phase is open at a time. A phase closes when every issue it opened is closed, or
is listed under Deferred below with a reason. The next phase opens only then. Each phase is
one pull request in each repository that it touches, and ends with the verification layer
that proves it.

## The first roadmap: the basic memory layer

| Phase | Delivers | Status |
|---|---|---|
| 0. Documents and measurements | The public contracts and guarantees files, CONTRIBUTING, the Makefile with the first documentation check, the local container setup, and measurements M1 to M6. | closed 2026-10-05, PR #2 |
| 1. The storage contract, offline | The record, the `Store` protocol, the in-memory driver, the conformance suite, the frozen surfaces, the ledger test, and CI. | closed 2026-10-06, PR #5 |
| 2. The Hotdata driver | `HotdataStore` with four tables, the serialized writer, the three-stage retrieval query, the sweeper, the integration leg against the local stack in CI, and the oracle test. | closed 2026-10-08, PR #7 |
| 3. The memory contract | `Memory` with remember, recall, candidates, supersede, forget, profile, and capture, the rendered blocks pinned by tests, and the skill file with its command check. | closed 2026-10-08, PR #9 |
| 4. The first consumer | A document loader in hotmemory. A post-mortem loader and a recall at investigation start in the consumer's own repository, proven by a replay case. Two pull requests, one in each repository. | closed 2026-10-10, PR #11 and the consumer's pull request |
| 5. A public benchmark | A LongMemEval-S runner measuring retrieval recall at k first and end-to-end accuracy second, with the numbers in `docs/benchmarks.md`. | closed 2026-10-12, PR #14 |

## The second workstream: memory accuracy

Agreed on 2026-10-13. `plan.md` holds the tasks, the acceptance criteria, and the stop
conditions of each phase. The library stays deterministic: no code in `src/hotmemory`
calls a model. Latency is not a goal, and its work waits under Deferred, except the
measurements at scale of phase 11. On 2026-10-16 the owner added phases 9 to 11, and the
later phases moved to 12 to 15. On 2026-10-19, after phase 10 closed, the owner paused the
workstream to work on other things first. On 2026-10-21 the owner resumed it with phase
11, to test hotmemory as a large memory store: recall from one store that holds 100,000
memories or more.

| Phase | Delivers | Status |
|---|---|---|
| 6. The accuracy loop | A second sample of 54 questions, and the memory arm configured from the command line. The diagnosis split for each question type, and the baselines of both samples. | closed 2026-10-14, PR #18 |
| 7. The extractor | A benchmark extractor that keeps what the assistant said, with sources, measured against the 19 misses where no stored fact held the answer. No library change. | closed 2026-10-14, PR #21 |
| 8. Episodes behind facts | `capture` stores its text as an episode that its facts cite. `recall` can add the cited or ranked episodes to its block, within one budget. | closed 2026-10-15, PR #24 |
| 9. Faster evaluation | The runner keeps the memory of a run and reuses it for runs that change only `recall` or the reader. A smoke run of 18 questions on `MemoryStore`. A run keeps its finished questions when it stops. No library change. | closed 2026-10-16, PR #28 |
| 10. The ranking, and episode chunks | An ablation of the fused ranking, and a default ranking chosen by the benchmark. `capture` stores a session as chunks, so the block holds the chunks that match and not whole sessions, for fewer reader tokens. | closed 2026-10-19, PR #30 |
| 11. A large memory store | Recall from one store that holds 100,000 memories or more. SQL `INSERT` measured in the cloud, and taken for appends if it is faster. The time of a write and a search at 10,000, 100,000, and 1,000,000 rows built from real text, by scope size, locally and in the cloud, with each top k checked against a brute-force ranking. Retrieval on LongMemEval-M. Samples A and B answered over a store prefilled with 100,000 or more memories, against their scores from a fresh store. LongMemEval-S in full: 439 of 500 on 2026-10-22. The large store on LongMemEval-M. A test of cues on facts. | open |
| 12. Supersede in capture | A fact can name the current record that it replaces, and `remember` closes that record. The extractor decides, and the library applies the rule. | proposed |
| 13. Search over history | `recall(as_of=T)` returns the revision valid at T, with the as-of filter in SQL, and a date window from the caller. The filter in SQL also keeps a block full when many records are dated after `as_of`, as phase 11 found. | proposed |
| 14. The full benchmark | The final configuration on all 500 LongMemEval-S questions, and a LoCoMo smoke test. The LongMemEval-S run of phase 11 is the first. | proposed |
| 15. Release on PyPI | The release flow of the sibling repository `hotdata-langchain`, and the first release. | proposed |

## Deferred

An entry here names the issue or the phase it came from, and the reason it is not being
taken up now.

- Adapters, planned as phase 6 of the first roadmap: a LangGraph `BaseStore` adapter in
  `hotdata-langchain`, then an MCP server. They wait until the memory contract scores well
  and is on PyPI.
- Latency, from phase 5: a conditional write for more than one writer, text loaded only
  for ranked rows, and the time of each cloud request. The second workstream measures
  accuracy first. Phase 11 measures SQL writes and the search at scale.
- Model calls, from the discussion of 2026-10-13: a model-backed extractor in an extra,
  and decision calls with a probability, for example to decide a supersede or to route a
  recall. The library stays deterministic in the second workstream.
- More extractor prompts, from phase 7. The prompt `detail` cut the misses with no
  stored fact from 19 to 16 on sample A, and the stop condition asked for 12 or fewer.
  The owner accepted it on 2026-10-14. With `detail`, the extractor still drops what the
  assistant said: 7 misses of the type single-session-assistant on sample A have no
  stored fact. A second extractor model was not tried, because the owner named none.
- Precise chunk citations, from phase 10. A fact that `capture` writes with chunks cites
  every chunk of its text, because the extractor does not say which chunk it came from.
  So `episodes="cited"` still gives the whole text. A match of the fact to its chunk, or
  an extractor that names it, can make `"cited"` save tokens too. Phase 10 measured
  `"search"` only.
- The first-page listing of `hotdata-framework`, from phase 10. `list_managed_databases`
  and `resolve_managed_database` read only the first 100 databases, and
  `HotdataStore.provision` uses the first. It is filed as
  hotdata-dev/sdk-python-framework#89. Phase 11 works around it in `provision`.
- The vector rankings on the vector index, from phase 2. A filtered search through the
  vector index misses rows loaded after the build. Until it is fixed, the vector rankings
  scan, and phase 11 measures that cost.
- SQL `UPDATE` and `DELETE`, from phase 11. The engine refuses them, so a closed
  revision, `forget`, and `sweep` stay on keyed loads.
- The comparison with other memory layers in one harness, such as MemoryBench, from
  phase 11. It needs an account and a cost for each service, and it does not test a
  large store.
- BEAM at 10 million tokens, from phase 11, as a later test of one very large store.
- The ideas of the hindsight study of 2026-10-09 that the second workstream does not
  take, for later planning:
  - A chunk-hash delta on re-capture.
  - "No time" kept distinct from "omitted".
  - Word and number checks on entity names.
  - A forget that cascades to derived records.
  - A parquet export.

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
  loaded after the build, so the vector rankings filter after their fetch. The defect
  also occurs in the cloud. One process writes to a
  database until the engine has a conditional write.
- Phase 3 closed on 2026-10-08 with PR #9, which closed issue #8. It changed the plan for
  later phases in four ways. The vector rankings of `HotdataStore` filter first and rank
  by a scan, so the vector indexes serve no query until the engine fixes the filtered
  index search. At 100,000 rows of 64 dimensions, a search in a 1 percent scope took 97 ms
  locally. `put` gained `close_previous`, and a put of the same content with a new source
  writes a merged revision. `recall` reads current revisions only, so a search over
  history is still open. A count in the block of `profile` is a lower bound when a
  sub-namespace fills the window of `list`.
- Phase 4 closed on 2026-10-10 with PR #11 and the consumer's pull request, which closed
  issue #10. The consumer loaded 12 post-mortems, and counted 129 episodes and 256 facts
  with SQL. A second load wrote nothing. It changed the plan for later phases in four ways. The pass
  rule of the replay case judges the investigation loop from its trace, because the report
  step drops the date of a rejected hypothesis. A consumer that renders its own block
  passes `budget` and `k` to `recall`, because the default budget of 2,000 characters cut
  the list first. A single-part upload in the Hotdata SDK does not retry on a closed
  connection, so a long load can fail and must be run again. It is reported to the SDK
  repository. A document that gets fewer chunks keeps its old last episodes current.
- Phase 5 closed on 2026-10-12 with PR #14, which closed issue #13. On LongMemEval-S, an
  evidence session was in the top 5 for 98.3 percent of the 419 scored questions, with
  the vector ranking. On a sample of 54 questions, the reader scored 39 with every
  session, 45 with the top 10 sessions, and 19 with the block of `recall`. It changed the
  plan for later phases in four ways. The extractor is the main loss of the memory
  contract: in 19 of its 35 misses, no stored fact held the answer. On this data the
  vector ranking scored higher than the fused ranking, and the reason was not measured.
  With every question in one table, the fused ranking scored higher at the top 5 and
  slightly lower at the top 10. A BM25 ranking inside the scope can wait. In the cloud, a
  fused search took 998 ms at p50 and a vector search 361 ms.
- Phase 6 closed on 2026-10-14 with PR #18, which closed issue #16. A replay of sample A
  from the caches reproduced 19 of 54 and each cause of each miss, at no model cost. On
  sample B, the reader scored 43 with every session, 48 with the top 10 sessions, and 27
  with the block of `recall`. It changed the plan for later phases in four ways. The
  memory arm differs by 14.8 points between the two samples with the same configuration,
  so a change counts only if both samples move the same way. The extractor is the main
  loss on both samples: no stored fact held the answer in 19 of 35 misses on sample A and
  16 of 27 on sample B. When the block held a needed fact, it held every needed fact in
  only 2 of 25 misses. A fact ranked below 30 caused 2 misses on sample A and none on
  sample B, so the ranking has little to gain on these samples. The runner gained
  `--replay` and `--workers`, and a replay of the memory arm of sample A took 519 s with
  4 workers, against 2,264 s with one process.
- Phase 7 closed on 2026-10-14 with PR #21, which closed issue #19. With the extractor
  prompt `detail`, the memory arm scored 31 of 54 on sample A and 32 on sample B, over
  the pass level of 28 on each sample. The `baseline` prompt with sources scored 22 on
  sample A. It changed the plan for later phases in five ways. Each fact of the benchmark
  carries the source `session:` and a hash of the session id, because the block shows the
  sources to the reader and LongMemEval names each evidence session `answer_...`. The
  sources change the extractor prompt, so the extractor cache of phase 6 does not replay
  with them. Most of the gain came from facts that are more complete: 9 of the 13 gains on
  sample A were misses where the block of the baseline held some of the needed facts. The
  misses with no stored fact went from 19 to 16 on sample A and from 16 to 14 on sample
  B, and they are still the largest group. The marker of the diagnosis can miss facts,
  so that group can be smaller than the diagnosis shows. A reply that reaches the token
  limit gives no facts for its session, and a result file counts such sessions.
- Phase 8 closed on 2026-10-15 with PR #24, which closed issue #22. `capture` stores a
  session as an episode that its facts cite, and `recall` adds episodes after the facts.
  With `episodes="search"` at 64,000 characters, the memory arm scored 45 of 54 on sample
  A and 45 on sample B, against 29 and 31 with facts only, at about 12,800 reader tokens
  at the median. The retrieval arm scored 45 on sample A with 29,878 tokens. It changed
  the plan for later phases in four ways. The misses with no stored fact went from 17 to
  5 on each sample, and in 8 of the 9 misses on each sample the block held an evidence
  session, so the reader is now a larger loss than the extractor. The block shows a
  source in its own namespace as `<key>@<revision>`, so the run id stays out of every
  prompt and a run replays `capture` from the cache. Each run captured the same memory
  again for about 20 minutes, which led to phase 9. Whole sessions cost the tokens, which
  led to the chunks of phase 10. An internet outage stopped four runs, and the local
  engine refused one search with a stale BM25 index, which a second try did not show.
- Phase 9 closed on 2026-10-16 with PR #28, which closed issue #25. With `--keep`, the
  runner keeps the memory of each question in a local database of its own, and a run with
  `--reuse` recalls from it with no `capture`. On sample A, each reused run took 11 to 15
  seconds and equaled its phase 8 run on all 54 rows. The phase 8 runs took 1,133 to
  2,085 seconds. The smoke run of 18 questions on `MemoryStore` took 12.7 seconds from
  the cache. Its first run cost $1.07 and took 2,080 seconds in one process, and it now
  takes `--workers`. A run keeps its finished questions in a rows file, and `--resume`
  completes it. It changed the plan for later phases in three ways. One database holds one
  question, because BM25 ranks the whole table before the namespace filter. The same
  order can cost a `recall` over a narrow scope in a shared table, so phase 11 measures
  it. A commit that changes `src/hotmemory` makes the kept memory unusable, so a phase
  that changes the library captures once again for each configuration. `MemoryStore`
  ranks by vector only, so the first smoke run of a configuration pays for extraction.
- Phase 10 closed on 2026-10-19 with PR #30, which closed issue #26. An ablation on the
  419 retrieval questions showed that the BM25 arm made the whole gap between the
  rankings: `recall_all@10` was 0.9570 fused, 0.9928 by vector, and 0.8926 for BM25
  alone. So `HotdataStore` ranks by vector by default, as `MemoryStore` does, and
  `ranking="fused"` stays as an option. `capture` takes `chunk_chars` and stores a session
  as chunks, and each fact cites every chunk. The block shows a run of chunk sources as one
  range. With chunks of 1,000 characters at a budget of 16,000, the memory arm scored 48
  of 54 on sample A and 46 on sample B, at about 4,350 reader tokens at the median. Phase
  8 scored 45 on each with about 12,800 tokens. Phase 10 spent $12.77. It changed the plan
  for later phases in three ways. The extractor reads a known-fact list of 2,000
  characters, so a longer block line changes its prompt, and a run with chunks paid for
  part of its extraction again. A run that reuses kept memory at a new budget pays for
  the reader and the judge. The framework listing reads only the first 100 databases,
  which the runs worked around by deleting each set of kept databases before the next.
