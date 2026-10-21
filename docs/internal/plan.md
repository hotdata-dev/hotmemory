# Plan: the second workstream, memory accuracy

Status: agreed, 2026-10-13. The owner agreed to each decision and to the target on
2026-10-13. Phase 8 closed on 2026-10-15 with PR #24. On 2026-10-16 the owner added
three phases after it: faster evaluation, the ranking with episode chunks, and a large
memory store. The later phases moved to 12 to 15. Phase 9 closed on 2026-10-16 with PR
#28. Phase 10 closed on 2026-10-19 with PR #30. On 2026-10-19 the owner paused this
workstream before phase 11, to work on other things first. On 2026-10-21 the owner
resumed it with phase 11, a large memory store. This file
holds the phases still to come, 11 to 15. Each phase becomes one GitHub issue and one pull request, worked in task order. When a phase closes, `roadmap.md` gets
its paragraph, and this file keeps only the phases still open. The plan of phase 5 is in
git history, at commit `4a6fb36`. The plan of phase 6, with the decisions taken during
its work, is at commit `8a7318d`. The plan of phase 7 is at commit `ab93da1`. The plan
of phase 8, with the decisions of its task 1, is at commit `b5443ac`. The plan of phase 9
is at commit `2df6881`. The plan of phase 10 is at commit `a0145ee`.

## Goal

When this workstream closes, the memory arm meets the target below, and hotmemory is on
PyPI. At the start of the workstream, the memory arm scored 19 of 54 on sample A (35.2
percent). After phase 8, it scores 45 of 54 on sample A and 45 on sample B, with
episodes after the facts. After phase 10, with chunks of 1,000 characters at a budget of
16,000, it scores 48 on sample A and 46 on sample B, at about 4,350 reader tokens. The retrieval arm scored 45 of 54 on sample A.

The target (agreed) is a distance from the lowest published score of a memory layer with
a GPT-4o reader: Zep at 71.2 percent (read, not reproduced). The published range goes up
to 84.2 percent.

| Level | Distance from 71.2 percent | Sample A and sample B | All 500 questions |
|---|---|---|---|
| Pass | At most 20 points | At least 28 of 54 on each | At least 51.2 percent |
| Goal | At most 10 points | At least 33 of 54 on each | At least 61.2 percent |

The workstream closes on the pass level, measured in phase 14 on all 500 questions. The
sample counts guide the phases before it. A sample of 54 is uncertain by about 10 points,
so the number on all 500 questions decides.

The library stays deterministic. No code in `src/hotmemory` calls a model. The extractor
and the embedder stay callables that the caller passes. The benchmark owns its extractor
in `bench/`.

Latency is not a goal of this workstream. Each phase records the time of its searches and
writes, and does not try to reduce it. The roadmap lists the latency work under Deferred.

## Where we start

All numbers are from `docs/benchmarks.md`. The rows of sample A were observed on
2026-10-10, and the rows of sample B on 2026-10-14. The rows of phase 7 were observed on
2026-10-14, the rows of phase 8 on 2026-10-15, and the rows of phase 10 on 2026-10-16
and 2026-10-18.

| Item | Value |
|---|---|
| Retrieval, vector ranking, 419 questions | `recall_any@5` 0.9833, `recall_all@10` 0.9928 |
| Retrieval, fused ranking, 419 questions | `recall_any@5` 0.9690, `recall_all@10` 0.9570 |
| Sample of 54, full context | 39 correct, 113,689 reader tokens at the median |
| Sample of 54, retrieval arm, top 10 sessions | 45 correct, 29,878 reader tokens |
| Sample of 54, memory arm | 19 correct, 1,462 reader tokens |
| Memory misses, no stored fact held the answer | 19 of 35 |
| Memory misses, stored but ranked below 30 | 2 of 35, both ranked higher by the vector ranking |
| Memory misses, a needed fact was in the block | 14 of 35: 1 with every needed fact, 13 with some |
| Sample B, full context, retrieval, and memory arms | 43, 48, and 27 correct of 54 |
| Sample B memory misses, no stored fact held the answer | 16 of 27 |
| Sample B memory misses, stored but ranked below 30 | 0 of 27 |
| Sample B memory misses, a needed fact was in the block | 11 of 27: 1 with every needed fact, 10 with some |
| Phase 7, memory arm with `detail` and sources, sample A | 31 correct, 1,655 reader tokens at the median |
| Phase 7, memory arm with `detail` and sources, sample B | 32 correct, 1,664 reader tokens at the median |
| Phase 7 misses with `detail`, no stored fact held the answer | 16 of 23 on sample A, 14 of 22 on sample B |
| Phase 7 misses with `detail`, the block held some needed facts | 6 of 23 on sample A, 7 of 22 on sample B |
| Phase 8, facts only, the baseline of phase 8 | 29 on sample A, 31 on sample B, 1,715 reader tokens at the median |
| Phase 8, `episodes="search"` at 64,000 characters | 45 on sample A, 45 on sample B, 12,849 and 12,676 reader tokens |
| Phase 8, `episodes="search"` at 32,000 characters | 43 on sample A, 5,715 reader tokens |
| Phase 8 misses with `search` at 64,000 characters | 9 on each sample: 5 with no stored fact, and an evidence session in the block in 8 |
| Phase 10, retrieval, fused ranking less BM25 | the same top 10 as the vector ranking on 419 of 419 questions |
| Phase 10, memory arm, vector ranking, `search` at 64,000 characters | 46 on sample A, 13,405 reader tokens |
| Phase 10, chunks of 1,000 characters, `search` at 16,000 characters | 48 on sample A, 46 on sample B, 4,336 and 4,360 reader tokens |
| Phase 10 misses with chunks of 1,000 at 16,000 | 6 on sample A and 8 on sample B, 2 with no stored fact on each |
| Phase 8 run time on sample A, 4 workers, from `duration_s` in the result files | 1,145 s for facts only, 1,507 s for `search` at 64,000 characters, 1,133 to 2,085 s over the seven runs |

Four facts about the memory arm, read from `bench/e2e.py` and `src/hotmemory/contract.py`
at the start of the workstream, shaped its first phases. Phases 8 and 10 changed facts 2
and 4:

1. The extractor is `extractor` in `bench/e2e.py`. It sends each session to
   `gpt-4o-mini-2024-07-18` with a prompt variant from `VARIANTS` in `bench/prompts.py`,
   and returns `Fact(kind="fact")` with a subject, content, and the source
   `session:<hash>`. The hash is the first 12 hex digits of the SHA-256 of the session id.
   From phase 7, the memory arm runs with `--extractor detail`.
2. `capture` stores facts only. It does not store the session, so the memory arm keeps no
   session text in the store. From phase 8, `capture` with `episode` also stores the
   session as an episode, and from phase 10 as chunks with `chunk_chars`.
3. `remember` derives each key from the subject and a hash of the content. A changed value
   gets a new key, so the old fact stays current beside the new one.
4. `recall` searches the kinds `fact`, `profile`, and `procedure`, keeps k records, and
   then applies `as_of` in the process. It never returns an episode. From phase 8, it adds
   episodes after the facts when the caller passes `episodes`.

## Decisions

The owner agreed to each item on 2026-10-13.

- Deterministic library (agreed). No model call enters `src/hotmemory`. A model-backed
  extractor in an extra, and decision calls such as a classifier for supersede, wait in
  Deferred until this workstream closes.
- Two samples (agreed). Sample A is the committed sample of 54, seed `20261010`, in
  `bench/sample.json`. Each phase iterates on sample A. Sample B is a second sample of 54,
  9 of each type, with a new fixed seed, and with no question of sample A. A phase runs
  sample B once, at its end, to check that a gain on A is not a fit to A.
- What counts as a gain (agreed). One question moves an arm by 1.9 points on 54
  questions. Two conditions make a change count. Sample A and sample B move the same way.
  The diagnosis shows the targeted group of misses getting smaller. A phase reports every
  variant it tried, not only the best one.
- Fixed models (agreed). The reader and the judge stay `gpt-4o-2024-08-06` at
  temperature 0, with the reference prompts. The extractor stays
  `gpt-4o-mini-2024-07-18`. From phase 7, its prompt is `detail`. Phase 7 tried no other
  extractor model, because the owner named none.
- The full-context arm (agreed). It runs once on sample B, in phase 6, as the baseline
  of B. No later phase runs it again on 54 questions. It costs the most, at about 114,000
  tokens per question.
- The cost limit (agreed in phase 5, kept). The runner prints an estimate and stops over
  50 dollars. Each phase records what it spent.
- The stack (kept). End-to-end runs use `HotdataStore` on the local stack, with the vector
  ranking, its default from phase 10. A phase that
  changes a driver runs `make integration` and keeps the oracle test of `MemoryStore`
  against `HotdataStore`.
- Changes to a surface (kept). A change to a frozen surface updates `CHANGELOG.md`,
  `docs/contracts.md`, and `docs/guarantees.md` in the same commit, and `MemoryStore` gets
  the same behavior as `HotdataStore`.

## Phase 11. A large memory store

Goal: proof that hotmemory recalls well from a store that already holds many memories.
The owner set the aim on 2026-10-21: one store with 100,000 memories or more, where
search stays fast and exact, and where the right memory still comes back. The phase
gives numbers at 10,000, 100,000, and 1,000,000 rows: the time of a write and of a
search, the accuracy of retrieval, and the accuracy of answers from a shared store.

A large store here means many memories in the store during a run. It does not mean only
many questions. The owner's hypothesis: hotmemory does well at 100,000 to 1,000,000
memories now that the engine accepts SQL writes. A second hypothesis, also to test: cues
help retrieval on facts. In phase 10 the fused ranking lost to the vector ranking on
episodes, but episodes have no cues.

What the research of 2026-10-21 found (read from source and the public datasets, not
observed unless marked):

- The engine accepts `INSERT` and `CREATE TABLE` on managed tables, with no setting,
  since 2026-10-18. It refuses `UPDATE`, `DELETE`, `MERGE`, `ON CONFLICT`, and
  `RETURNING`. No work on `UPDATE` or `DELETE` has started. Whether the cloud accepts
  `INSERT` is not observed. An `INSERT` goes through the same append path as a load.
- `execute_sql` of `hotdata-framework` 0.14.1 retries a dropped connection up to 3
  times, and the engine does not drop a repeated `INSERT`. So a retry can write a row
  twice.
- Each write reads the current revision of each key first. With every question in one
  table of 19,864 rows, a write took 1.91 s at p50, against 0.29 s with one question per
  table [observed, phase 9].
- Of the writes of `HotdataStore`, an `INSERT` can take over rows that are all first revisions, new cue rows, and the seed
  and meta rows. A closed old revision, `forget`, and `sweep` still need keyed loads.
- With the vector ranking, a shared table gave the same sessions in the same order as one
  question per table [observed, phase 9]. So a shared store with the vector ranking
  keeps the capture cache, and one store can hold every question.
- LongMemEval-M has about 500 sessions for each question. Its file is 2.74 GB, under the
  MIT license. All 500 questions of LongMemEval-S in one store give about 150,000 memory
  rows. BEAM, with conversations of 1 million and 10 million tokens, is the other public
  test of a large store.
- The filtered vector index defect is still open in the engine, so the vector rankings
  keep their scan in this phase.

Unit costs for the estimates, from the phase 10 result files: capture costs about
$0.0005 and 1.06 s for each session with 4 workers. The reader and the judge cost about
$0.015 for each question at a budget of 16,000. Only the run of 54 questions on
LongMemEval-S is observed. Every other estimate below is extrapolated from these.

Tasks:

1. SQL writes in the cloud. Send one `INSERT` to a throwaway cloud database, and run M4
   again: one hundred single-row `INSERT` statements against one hundred single-row
   loads, then the same at flushes of 100 and 1,000 rows of 1,536 dimensions. Record
   the size limit of a query request if one shows. Correct the claims that the cloud
   refuses SQL writes in `docs/guarantees.md`, `docs/internal/architecture.md`, and
   `docs/internal/brief.md`. Remove the retired `RUNTIMEDB_ENGINE__SQL_WRITES` from
   `compose.yaml` and `scripts/measure_local.py`. No model cost. About 15 minutes.
2. An `INSERT` path in `HotdataStore`, only if task 1 shows a clear gain. A flush whose
   rows are all first revisions, and the new cue rows, go by `INSERT`. Rows that close a
   revision stay on the upsert load. The path must not write a row twice on a retry:
   either send the statement with no retry, or check by id before a second try. A flush
   that mixes both kinds of row sends the upsert load last, so a crash leaves no second
   current revision. Update `docs/contracts.md` and `docs/guarantees.md` in the same
   commit, with tests in the conformance suite and `make integration`.
3. `provision` reads every page of databases. It pages the raw API instead of
   `list_managed_databases`, so a workspace with more than 100 databases does not get a
   second database with one name. This works around
   hotdata-dev/sdk-python-framework#89 until the framework fixes it.
4. The scale harness, a script outside the package. It builds a store from real text,
   not random vectors:
   - The rows: LongMemEval sessions as chunks of 1,000 characters, and the facts that
     earlier runs extracted, from the reply cache. Sessions of LongMemEval-M fill the
     store past the rows of LongMemEval-S. No row is a copy of another.
   - The embeddings: `text-embedding-3-small` at 1,536 dimensions, through the bench
     cache.
   - The scopes: namespaces that hold 1, 10, and 100 percent of the rows.
   - The questions: a fixed set of LongMemEval questions whose evidence sessions are in
     the store.
   
   At each checkpoint of 10,000, 100,000, and 1,000,000 rows, the script records:
   - The write rate with loads, and with `INSERT` if task 2 took it, and the time of the
     read before each write.
   - The search time at p50 and p95, by vector and fused, at each scope size.
   - For each search, whether the top k equals a brute-force top k in Python over the
     same rows. While the rankings scan, any difference is a defect.
   - The `recall_any@k` against the evidence sessions.
   
   It runs on the local stack first, then in the cloud. Embeddings for 1,000,000 rows
   cost about $2 to $5. The time of the load is not known. The script stops after
   10,000 rows, and the owner gets the extrapolated time of 100,000 and 1,000,000 rows
   before it goes on.
5. Retrieval on LongMemEval-M. Score `recall_any@k` with no reader model in two forms:
   one shared table with a namespace for each question, and one pool of the unique
   sessions, where the sessions of other questions are distractors. The vector ranking
   first, and the fused ranking beside it. About $1.60 of embeddings, and 1.5 to 2.5
   hours on the local stack. The 2.74 GB file stays out of git.
6. Answers from one shared store. The runner gains a mode that keeps every question in
   one database, with a namespace for each question, and the vector ranking.
   - First, sample A captures into the shared store from the cache. Each row must equal
     its phase 10 row, at no model cost. If a row differs, stop and diagnose.
   - Then the other questions of LongMemEval-S capture into the same store, to about
     150,000 rows. About $9 to $10 for capture, because about 108 questions are in the
     cache, and $1.20 for embeddings. About 5.5 to 7 hours.
   - Then samples A and B answer from the full store at the phase 10 configuration.
     About $1.60 for the reader and the judge.
7. The cue test on facts. Compare the fused ranking, with cues, and the vector ranking on
   facts only, with one question for each database, on samples A and B. The fused ranking
   changes the known facts that the extractor reads, so part of capture pays again. Up to
   about $2 for each sample, and about 45 minutes for each.
8. Record the numbers in `docs/benchmarks.md`, the measured claims in
   `docs/guarantees.md` and `docs/internal/architecture.md`, and the phase in
   `roadmap.md`.

The phase spends about $20 to $25 in all, under the cost limit of $50. Each paid or long
run stops first for the owner's yes, with its estimate.

Acceptance:

- Each number of task 4 at 10,000 and 100,000 rows, locally and in the cloud, and at
  1,000,000 rows if the owner agrees to its time.
- Every brute-force check of task 4 passes.
- `recall_any@k` on LongMemEval-M, in both forms of task 5.
- Samples A and B answered from a store of about 150,000 rows, next to their phase 10
  scores of 48 and 46.
- The cue test answers whether the fused ranking beats the vector ranking on facts.

Stop conditions:

- If the shared replay of sample A differs from phase 10, stop before more capture.
- If the extrapolated load of 1,000,000 rows takes more than one night, stop and ask.
- If the cloud refuses `INSERT`, task 2 drops, and the phase goes on with loads.

Not in this phase, under Deferred in `roadmap.md`:

- The vector rankings on the vector index. They wait for the engine's fix of the filtered
  search. When it lands, tests must prove that every write, revision, and delete is found
  through the vector index and the BM25 index.
- `UPDATE` and `DELETE` by SQL, until the engine has them.
- The comparison with other memory layers, such as MemoryBench. It needs an account and a
  cost for each service, and it does not test a large store.
- BEAM at 10 million tokens.

## Phase 12. Supersede in capture

Goal: when a session changes a fact, the old fact closes, so the reader sees one current
value. Today a changed value gets a new key, and both values stay current.

The library decides nothing by itself. The extractor decides which fact a new fact
replaces, and the library applies that decision with the rules of `supersede`.

Tasks:

1. Propose the surface in `docs/contracts.md` and stop for the owner's agreement. The
   proposal below is a starting point:
   - `Fact` gains `replaces`, the key of a current record in the same scope. `remember`
     writes such a fact as the next revision under that key, with `close_previous`, at
     the `valid_from` of the fact. A key that is not current raises `ValueError`, and the
     batch writes nothing.
   - The extractor gets the keys of the current records that `capture` passes to it.
     `capture` can also pass the `candidates` of each extracted fact, but that needs a
     second extractor call, so it waits for the owner.
2. Build the agreed surface for both drivers, with conformance tests that use a fixed
   extractor and no model: a changed value, a value that changes twice in one capture, a
   `replaces` that names a closed record, and a retried capture.
3. The benchmark extractor prompt gets the keys, and it can set `replaces`.
4. Report the knowledge-update type on both samples, and count the facts closed per
   question. A working supersede still moves the benchmark little. The judge
   of LongMemEval accepts an answer that gives both the old value and the new one.
5. Add a small offline test set of update pairs to `bench/`, written by hand from
   knowledge-update sessions. It checks that the extractor sets `replaces` where a value
   changed, with the cached extractor replies.

Acceptance criteria:

- A changed value leaves one current record and one closed record with `valid_until` and
  `expired_at`, in both drivers.
- `recall` with `as_of` before the change returns the old value only after phase 13.
  This phase states that limit in `docs/contracts.md`.

Stop and ask if:

- The extractor sets `replaces` on records that did not change in more than one case of
  ten in the offline test set.

## Phase 13. Search over history

Goal: `recall(as_of=T)` returns what was true at T, including a fact that a later
revision replaced. Today it reads current revisions only, and it applies `as_of` after
it keeps k records.

Tasks:

1. Write the rule in `docs/contracts.md` and stop for the owner's agreement. The rule
   picks, for each key, the revision valid at T. A revision closed without
   `close_previous` has no `valid_until`. The rule needs a second test for such a
   revision, for example the `created_at` of the next revision. The proposal names that
   test.
2. Move the as-of filter into the SQL of both rankings, so that k counts records valid at
   T. `MemoryStore` gets the same order.
3. Add a caller-supplied date window to `recall` that filters records by their validity,
   with no date parsing in the library.
4. Conformance tests for each case of the rule, on both drivers, and the oracle test.
5. Report temporal-reasoning and knowledge-update on both samples. Every session of a
   LongMemEval question comes before the question, so this phase is expected to move the
   benchmark little. Its proof is the conformance suite and the replay case of the first
   consumer.

Acceptance criteria:

- A fact replaced after T is in the block of `recall(as_of=T)`, and a fact replaced
  before T is not, in both drivers.
- If at least k records are valid at T, the search with `as_of` returns k records.

Stop and ask if:

- The rule needs a new record field or a new `Store` method.
- The as-of filter in SQL makes a local search slower than twice its time without the
  filter.

## Phase 14. The full benchmark and a second benchmark

Goal: the final configuration has numbers on all 500 questions, and on a second
benchmark.

Tasks:

1. Run the retrieval arm and the memory arm on all 500 questions, with the configuration that
   phases 7 to 13 agreed. Print the estimate first. The run needs the owner's yes, because
   it costs about ten times a sample run.
2. Add a LoCoMo runner to `bench/`, as a smoke test of 10 conversations and 1,540
   questions. Its labels have known errors, which the research notes list. The report
   states this.
3. Update `docs/benchmarks.md` and the README section "Benchmark", with the configuration
   of each number.

Acceptance criteria:

- `docs/benchmarks.md` reports both benchmarks, each number with its configuration, and
  the phase 5 numbers stay as the record of their configuration.
- On all 500 questions, the memory arm scores at least 51.2 percent, the pass level of
  the goal. The report states whether it reaches the goal level of 61.2 percent.

Stop and ask if:

- An estimate is over 50 dollars. The full run needs the owner's yes either way.
- The memory arm scores below the pass level on all 500 questions. The owner then decides
  whether the workstream closes or gets another phase.

## Phase 15. Release on PyPI

Goal: `pip install hotmemory` installs the first release.

Tasks:

1. Copy the release flow of the sibling repository `hotdata-langchain`:
   - `RELEASING.md`, and `scripts/release.sh` with `prepare` and `publish`.
   - A workflow that publishes on a `vX.Y.Z` tag through PyPI trusted publishing.
   - A workflow that writes the GitHub Release from `CHANGELOG.md`.
   - A check that refuses a version change without a matching changelog section.
2. Check the wheel and the sdist: no `bench/`, no tests, no `.env`, and no
   `docs/internal/`. Make the README links absolute, so that they work on PyPI.
3. The owner configures trusted publishing on PyPI, creates the `pypi` environment on
   GitHub, picks the version, and runs the publish.

Acceptance criteria:

- A clean environment installs the release with each extra, and the examples of the
  README run.

## Not in this workstream

These wait, and `roadmap.md` lists them under Deferred:

- Latency: a conditional write, text loaded only for ranked rows, and the per-request
  time of the cloud. Phase 11 measures SQL writes and the search at scale.
- A model-backed extractor in an extra, and decision calls with a probability, such as a
  classifier that decides supersede or routes a recall.
- The other ideas from the hindsight study of 2026-10-09: a chunk-hash delta on
  re-capture, "no time" distinct from "omitted", word and number checks on entity names,
  forget cascading to derived records, and a parquet export.
- Adapters: a LangGraph `BaseStore` adapter and an MCP server.

## Verification

- Per commit: `make verify`.
- Per commit that touches a driver: `make local-up`, then `make integration`.
- Each benchmark run writes its configuration and results to one file in `bench/results/`.
  The numbers in `docs/benchmarks.md` come from those files.
- Before each pull request: the docs audit of the `pr-workflow` skill, and a search of the
  diff for the names of private repositories and deployment details.

## Questions for the owner

- Phase 11: the cloud workspace for the run at 1,000,000 rows, and its cost limit apart
  from the model spend.
- Phase 11: a target for the search time, such as p95 in the cloud at 1,000,000 rows.

- Phase 15: the first version number, and whether to publish to TestPyPI first.
