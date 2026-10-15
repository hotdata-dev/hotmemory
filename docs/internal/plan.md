# Plan: the second workstream, memory accuracy

Status: agreed, 2026-10-13. The owner agreed to each decision and to the target on
2026-10-13. Phase 7 closed on 2026-10-14 with PR #21. Phase 8 is open, as issue
#22. This file holds the open workstream, phases 8 to 13. Each phase becomes one GitHub
issue and one pull request, worked in task order. When a phase closes, `roadmap.md` gets
its paragraph, and this file keeps only the phases still open. The plan of phase 5 is in
git history, at commit `4a6fb36`. The plan of phase 6, with the decisions taken during
its work, is at commit `8a7318d`. The plan of phase 7 is at commit `ab93da1`.

## Goal

When this workstream closes, the memory arm meets the target below, and hotmemory is on
PyPI. At the start of the workstream, the memory arm scored 19 of 54 on sample A (35.2
percent). After phase 7, it scores 31 of 54 on sample A and 32 on sample B. The retrieval
arm scored 45 of 54 on sample A.

The target (agreed) is a distance from the lowest published score of a memory layer with
a GPT-4o reader: Zep at 71.2 percent (read, not reproduced). The published range goes up
to 84.2 percent.

| Level | Distance from 71.2 percent | Sample A and sample B | All 500 questions |
|---|---|---|---|
| Pass | At most 20 points | At least 28 of 54 on each | At least 51.2 percent |
| Goal | At most 10 points | At least 33 of 54 on each | At least 61.2 percent |

The workstream closes on the pass level, measured in phase 12 on all 500 questions. The
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
2026-10-14.

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

Four facts about the memory arm, read from `bench/e2e.py` and `src/hotmemory/contract.py`,
shape the phases below:

1. The extractor is `extractor` in `bench/e2e.py`. It sends each session to
   `gpt-4o-mini-2024-07-18` with a prompt variant from `VARIANTS` in `bench/prompts.py`,
   and returns `Fact(kind="fact")` with a subject, content, and the source
   `session:<hash>`. The hash is the first 12 hex digits of the SHA-256 of the session id.
   From phase 7, the memory arm runs with `--extractor detail`.
2. `capture` stores facts only. It does not store the session, so the memory arm keeps no
   session text in the store.
3. `remember` derives each key from the subject and a hash of the content. A changed value
   gets a new key, so the old fact stays current beside the new one.
4. `recall` searches the kinds `fact`, `profile`, and `procedure`, keeps k records, and
   then applies `as_of` in the process. It never returns an episode.

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
- The stack (kept). End-to-end runs use `HotdataStore` on the local stack. A phase that
  changes a driver runs `make integration` and keeps the oracle test of `MemoryStore`
  against `HotdataStore`.
- Changes to a surface (kept). A change to a frozen surface updates `CHANGELOG.md`,
  `docs/contracts.md`, and `docs/guarantees.md` in the same commit, and `MemoryStore` gets
  the same behavior as `HotdataStore`.

## Phase 8. Episodes behind facts

Goal: `recall` can give the reader the source text of its facts. The retrieval arm shows
that whole sessions answer 45 of 54. This phase lets the memory contract use them, within
a budget.

The target is the two largest groups of misses after phase 7. With `detail`, no stored fact
held the answer in 16 of 23 misses on sample A and 14 of 22 on sample B. The block held
some of the needed facts in 6 and 7. An episode holds the turns that the extractor
dropped, so it can answer both groups.

This phase changes the library. The memory arm runs with `--extractor detail` and fact
sources, the configuration of phase 7. Its results are the baseline of this phase: 31 of
54 on sample A and 32 on sample B.

What phase 7 left for this phase, read from `bench/e2e.py` and `src/hotmemory/contract.py`,
not observed:

- Each benchmark fact carries the source `session:<hash>`, set by the extractor in
  `bench/e2e.py`. `load` adds the id of its episode to the sources of each fact, and the id
  is `<namespace>/<key>@<revision>`. If `capture` does the same, each fact gets a second,
  longer source.
- The block shows the sources to the reader, and `capture` passes the block of `recall` to
  the extractor as "Facts already in memory". A change to the sources changes both
  prompts, so the extractor cache of phase 7 does not replay. Phase 7 observed this for
  its own change. A run with a new source pays for the extractor again, at about the cost
  of a run of phase 7.
- The key of the episode must not show the session id, for the reason of phase 7:
  LongMemEval names each evidence session `answer_...`, and the reader sees the sources.

Tasks:

1. Propose the surface in `docs/contracts.md` and stop for the owner's agreement. The
   proposal below is a starting point:
   - `capture` takes an optional `episode` key. With it, `capture` stores the text as an
     episode under that key, and adds the episode id to the `sources` of each fact. This
     is what `load` does for each chunk, applied to one text.
   - `recall` takes an optional `episodes` argument with three values: none (today), the
     episodes that the recalled facts cite, or the episodes that a search over episodes
     ranks first. The block gives the facts first, then the episodes, each with its date.
     One `budget` covers both, and the facts never lose their place to an episode.
   - How a fact shows its episode. The proposal names the source string that the block
     shows, and whether the extractor of the benchmark still sets `session:<hash>`.
2. Build the agreed surface in `Memory`, for both drivers. The conformance suite, the
   ledger in `docs/guarantees.md`, the skill commands, and `CHANGELOG.md` follow.
3. The memory arm captures each session with an episode key, the hash of phase 7. Then it
   recalls with each `episodes` value, at three budgets: 16,000, 32,000, and 64,000
   characters. The search inside `capture` does not change with these values, so each
   run after the first replays `capture` from the extractor cache (read from source, not
   observed). Only the reader and the judge pay for each value and budget.
4. `bench/diagnose.py` reports, for each miss, whether the block held an evidence session
   as an episode.
5. Report accuracy against reader tokens for each value on sample A, then the best one
   on sample B, with the diagnosis and the cost of each run.

Decisions of task 1, agreed by the owner on 2026-10-15. `docs/contracts.md` holds the
surface.

- `capture` takes an optional `episode` key. `remember` writes the facts, and then one
  `put` writes the episode, as in `load`. The proposal said one writer, but a flush of
  `HotdataStore` loads its tables at the same time, so the order of the two writes was
  not fixed (read from source, not observed). If the episode is current with the same
  content, `capture` calls no extractor, writes nothing, and returns no id.
- `recall` takes `episodes`: none, `"cited"`, or `"search"`. Both values read with the
  `Store` methods of today. `"search"` uses the same `k` as the facts.
- The block gives the facts first, then a line `episodes:`, then each episode under a
  header `## <key>@<revision> [observed: <observed_at>]`. It holds whole episodes only,
  and it stops before the first episode that does not fit.
- A source that is the id of a record in the same namespace shows as `<key>@<revision>`.
  So the run id of the benchmark is in no prompt, and a run after the first replays
  `capture` from the cache. The blocks of documents that `load` stored get shorter.
- The benchmark extractor stops setting `session:<hash>`. The episode key is
  `session-<hash>`, with the hash of phase 7. The first run pays for the extractor again.
- The budgets are 16,000, 32,000, and 64,000 characters. The reason for dropping 8,000 was
  an estimate that the facts of phase 7 leave no room for a whole session. The runs then
  showed blocks of facts of at most 5,626 characters, so the estimate was too high.

Acceptance criteria:

- `MemoryStore` and `HotdataStore` return the same block for the same calls, in the
  conformance suite.
- A capture with an episode writes the episode and the facts in one flush per table, and
  a retried capture writes nothing new.
- `docs/benchmarks.md` reports accuracy and reader tokens for each value and budget.
- The best value scores more than 31 of 54 on sample A and more than 32 on sample B, the
  results of phase 7.

Stop and ask if:

- The surface needs a new `Store` method or a new record field.
- The best value needs more reader tokens than the retrieval arm, 29,878 at the median.
  Then memory costs as much as retrieval, and the owner decides whether the trade holds.
- The estimate of a run is over 50 dollars.

## Phase 9. The ranking

Goal: the default ranking of `HotdataStore` is the one that scores best, and the reason
for the gap between the two rankings is known.

Tasks:

1. Explain the gap on the 419 retrieval questions, with no reader model. Run the fused
   query with one arm left out at a time: BM25 and content, content and cue, and BM25
   alone. Episodes have no cues, so in the retrieval run the cue arm adds nothing. Find
   which arm costs the most and on which question types.
2. Run the memory arm on sample A with each ranking, with the best configuration of
   phases 7 and 8.
3. Propose the default to the owner: the vector ranking, or a rebuilt fusion. A rebuilt
   fusion can rank BM25 inside the scope, or fuse two arms with no weights. Stop for the
   agreement.
4. Build the agreed change, with `CHANGELOG.md`, `docs/contracts.md`, and
   `docs/guarantees.md`. If the default changes, the oracle test and the benchmark runner
   follow.
5. Run the agreed ranking on sample B, and rerun the retrieval score on all 419 questions.

Acceptance criteria:

- `docs/benchmarks.md` reports the ablation of task 1 and the retrieval score of the
  agreed ranking.
- The search inside `capture` uses the same ranking as `recall`, and a test proves it.

Stop and ask if:

- The ablation shows that the gap comes from the data and not from an arm, for example
  that BM25 favors a long session.

## Phase 10. Supersede in capture

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
- `recall` with `as_of` before the change returns the old value only after phase 11.
  This phase states that limit in `docs/contracts.md`.

Stop and ask if:

- The extractor sets `replaces` on records that did not change in more than one case of
  ten in the offline test set.

## Phase 11. Search over history

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

## Phase 12. The full benchmark and a second benchmark

Goal: the final configuration has numbers on all 500 questions, and on a second
benchmark.

Tasks:

1. Run the retrieval arm and the memory arm on all 500 questions, with the configuration that
   phases 7 to 11 agreed. Print the estimate first. The run needs the owner's yes, because
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

## Phase 13. Release on PyPI

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

- Latency: native DML for writes, the filtered vector index, a conditional write, text
  loaded only for ranked rows, and the per-request time of the cloud.
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

- Phase 13: the first version number, and whether to publish to TestPyPI first.
