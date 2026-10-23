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

Status on 2026-10-22: open, on the branch `phase-11-large-store` from `main`. Tasks 1 to 6
are done, and the full LongMemEval-S run is done. The numbers are in `docs/benchmarks.md`,
in the sections "The full benchmark" and "A large memory store". The work so far goes into
one pull request. No issue exists for this phase. The next task is task 7, the large store
on LongMemEval-M, in the form that the owner chose on 2026-10-22.

Goal: proof that hotmemory keeps its accuracy when the memory layer is large. The owner set
the aim on 2026-10-21. A large store means many sensible memories in the layer, from
100,000 to 1,000,000, that recall can reach, and new memories added to it during the run.
It does not mean only many questions. The test that answers it runs samples A and B as in
phase 10, over a store that is filled first, and compares the scores with the phase 10
scores of 48 of 54 and 46 of 54 from a fresh store. The full LongMemEval sets add variety to
the evaluation, and the scale test is the volume test of the layer.

Two hypotheses to test. The owner's: hotmemory does well at 100,000 to 1,000,000 memories
now that the engine accepts SQL writes. The second: cues help retrieval on facts. In phase
10, the fused ranking lost to the vector ranking on episodes, which have no cues.

### Decisions of the owner on 2026-10-21

- The work runs on one branch, with the doc fixes after phase 10 as its first commit.
- `provision` builds no vector index by default. `vector_index=True` is an experimental
  option that builds the three vector indexes and searches through them. It stays, so the
  index can come back when the engine updates an index as data arrives and fixes the
  filtered search through it.
- The local stack runs the latest engine image, `sha256:75403e84eb47`, built 2026-10-19.
- Task 6 changes from "capture every question into one database" to the prefilled store
  below, because a namespace that recall never reads cannot change an answer.
- The numbers go into the docs now, and the commit waits for the later numbers.

### Decisions of the owner on 2026-10-22

- The background of task 6 holds only chunks of sessions that no history of samples A and
  B holds. The cached facts stay out, because they copy the facts of the questions.
- `provision` builds no sorted index by default. `sorted_index=True` builds it.
- LongMemEval-S runs in full, on new databases, as the comparable score.
- The large store test moves to LongMemEval-M, where each question has one consistent user.
  BEAM comes later.
- The runs move to a stable machine later.

### What the phase found

Each item was observed on 2026-10-21 unless marked.

- The cloud accepts SQL `INSERT`. One row with a vector took 531 ms by `INSERT` and
  1,682 ms by load. At 10 rows they were equal. At 100 rows the load was five times
  faster. At 1,000 rows the `INSERT` was refused with `Payload Too Large`, at 32 MB of SQL.
  The engine refuses `UPDATE` and `DELETE`.
- The engine applies the scope filter inside `bm25_search`, before its depth, locally and
  in the cloud. So BM25 loses no rows of a narrow scope in a shared table. The plan of
  phase 9 to measure that loss is void.
- A plain vector index is the main cost of a write. A load of 1,000 rows into a table of
  10,000 rows took about 8.5 s with the index and about 1 s without it, in the cloud.
- The read of the current revisions before a write once sent one condition for each key.
  On a flush of 2,020 rows, the local engine stopped with a stack overflow. The read now
  uses lists of at most 500 keys.
- A query result holds at most 10,000 rows. A load that is in flight when its process
  dies still commits.
- The local image of 2026-10-08 returned fewer than k rows for a vector search over 50,000
  rows or more, when the query selected `content`. The image of 2026-10-19 and the cloud
  did not.
- The scale test, over one pool of every LongMemEval-S session as chunks, up to 254,025
  rows: the vector ranking matched a brute-force ranking at every size. `recall_any@10`
  over the whole pool fell from 0.8588 at 10,000 rows to 0.4235 at 254,025 by vector, and
  to 0.4824 fused. A scoped search stayed fast: 199 ms at 1 percent of 254,025 rows. A
  flush of 1,000 rows grew from 1.3 s to 14.3 s, and the cause past the read was not
  measured.
- LongMemEval-M retrieval, 419 questions, each in its own namespace of about 470 sessions:
  `recall_any@5` 0.9189 fused and 0.9045 by vector. The two tied at `recall_any@10`, at
  0.9451. On LongMemEval-S the vector ranking scored higher on every metric. So the fused
  ranking gains as a store grows.
- Transient upload failures, `the upload transfer to storage failed before any response`,
  happened about once in every 100 cloud loads.
- Observed on 2026-10-21 and 2026-10-22: the sorted index on `created_at` made each write
  into a table of 188,893 rows take 8 to 20 s, for 1 row as for 12 rows. BM25 alone took
  about 1.4 s, and no index about 0.4 s. SQL `INSERT` paid the same as a load. Every write
  was visible at once to a read by id, a newest-first query, and `bm25_search`. So the
  engine rebuilds the index after each write [inferred]. This was the cause of the growth
  of a flush in the scale test.
- Samples A and B over a store with 188,893 chunks of other sessions scored 39 and 35,
  against 48 and 46 from a fresh store. Other users' chunks took the places of the
  question's chunks, and contradicted its facts. `recall` applies `as_of` after the top k,
  so 3 blocks were half empty.
- LongMemEval-S in full: 439 of 500, 87.8%. The 392 questions outside the samples scored
  88.0%.
- One write into the episode table was refused with `RESOURCE_LOCKED` after 8 tries in
  about 16 s, with one writer only. The cause was not found.

### Tasks

1. Done, `9e96753`. SQL writes in the cloud: `scripts/measure_local.py --only m4`, with
   flushes of 1, 10, 100, and 1,000 rows. The results are in `docs/guarantees.md`, M4.
2. Done, `59ac141`. `HotdataStore` writes a table by a guarded `INSERT` when its rows in
   the write are all new and number at most 5 (`INSERT_ROWS`). The `INSERT` writes only
   the ids that the table lacks, so a retry writes nothing twice. A refused `INSERT` makes
   the store write by load from then on.
3. Done, `129e501`. `provision` reads every page of the database list, so
   hotdata-dev/sdk-python-framework#89 no longer affects it.
4. Done, `d3300cc` and later. The scale harness, `bench/scale.py`. Results:
   `scale-local-local-new-engine-436c7217.json` and
   `scale-cloud-cloud-no-vector-index-ec9d7e68.json` in `bench/results/`.
5. Done, `f25d7ea`. Retrieval on LongMemEval-M: `python -m bench.retrieval --data m`. The
   file is in `bench/data/`, 2.74 GB, SHA-256 `9d79e552...`, and the loader keeps only the
   user turns. Result: `retrieval-m-hotdata-local-isolated-fused-vector.json`.
6. Done. The prefilled store: `bench/scale.py --prefill`, `bench/e2e.py --background`,
   the time of each write in each row, and `bench/diagnose.py` for a background run. The
   sorted index became an option. Samples A and B scored 39 and 35, and a diagnosis of 25
   misses explains the loss. Cost $1.56 for the runs and $0.30 for the diagnosis.
7. Done. LongMemEval-S in full: `bench/e2e.py --sample all`, after a replay of samples A
   and B on new databases that gave 48 and 46 at $0. 439 of 500. Cost $14.84.
8. Next. The large store on LongMemEval-M. Two arms on samples A and B, each question with
   its own namespace of about 500 sessions: the chunks alone, with no extracted facts, and
   the full memory arm on sample A only. The full arm on both samples costs about $46 in
   extractor calls [extrapolated], too close to the cap of $50. Write the cost and time
   estimates before each run.
9. The cue test on facts. Compare the fused ranking, with cues, and the vector ranking on
   facts only, on samples A and B. Up to about $2 for each sample if the fused ranking
   changes the capture prompt.
10. Record the numbers in `docs/benchmarks.md`, `docs/guarantees.md`, and
    `docs/internal/architecture.md`, close the phase in `roadmap.md`, and commit.

### How to run and check

- `make local-up` starts the local stack with a random `RUNTIMEDB_SECRET_KEY`. The engine
  image of 2026-10-19 refuses an empty key. To swap only the engine, run
  `RUNTIMEDB_SECRET_KEY="$(openssl rand -base64 32)" docker compose up -d --no-deps runtimedb`.
- Scale: `uv run --group bench --env-file .env python -m bench.scale --sizes 10000,100000`,
  with `--location cloud`, `--keep`, `--vector-index`, and `--tag`. It writes its result
  file after each size, and retries a flush three times after a transient upload error.
- Run one heavy job on the local engine at a time, or the timings mix.
- Kill a run with SIGINT, so that it deletes its database. SIGKILL leaves the database. A
  run started with `&` from a shell that is not interactive ignores SIGINT. Stop such a
  run with SIGTERM to its process group, then delete its worker databases by name.
- Ask the owner before you stop, restart, or delete anything that a run uses.
- The local databases `agent-memory` and `local-memory` stay untouched. Each held one
  memory row and one meta row before and after the engine upgrade.

### Not in this phase

These are under Deferred in `roadmap.md`:

- The vector rankings on the vector index by default. They wait for the engine's fix of
  the filtered search. When it lands, the expected failure
  `test_a_search_through_the_vector_index_finds_every_later_write` fails, and the index can
  become the default.
- `UPDATE` and `DELETE` by SQL, until the engine has them.
- The comparison with other memory layers, such as MemoryBench.
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

- Phase 11: the yes to build and run task 6, the prefilled store.
- Phase 11: the cloud workspace for a run at 1,000,000 rows, and its cost limit apart
  from the model spend. The cloud runs so far used the workspace of `.env`.
- Phase 11: a target for the search time, such as p95 in the cloud at 1,000,000 rows.

- Phase 15: the first version number, and whether to publish to TestPyPI first.
