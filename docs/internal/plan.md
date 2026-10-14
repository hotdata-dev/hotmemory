# Plan: the second workstream, memory accuracy

Status: agreed, 2026-10-13. The owner agreed to each decision and to the target on
2026-10-13. Phase 6 closed on 2026-10-14 with PR #18. Phase 7 is open, as issue
#19. This file holds the open workstream, phases 7 to 13. Each phase becomes one GitHub
issue and one pull request, worked in task order. When a phase closes, `roadmap.md` gets
its paragraph, and this file keeps only the phases still open. The plan of phase 5 is in
git history, at commit `4a6fb36`. The plan of phase 6, with the decisions taken during
its work, is at commit `8a7318d`.

## Goal

When this workstream closes, the memory arm meets the target below, and hotmemory is on
PyPI. The memory arm scored 19 of 54 on sample A (35.2 percent). The retrieval arm scored
45 of 54.

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
2026-10-10, and the rows of sample B on 2026-10-14.

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

Four facts about the memory arm, read from `bench/e2e.py` and `src/hotmemory/contract.py`,
shape the phases below:

1. The extractor is `bench/e2e.py:77`. It sends each session to `gpt-4o-mini-2024-07-18`
   with the prompt in `bench/prompts.py`, and returns `Fact(kind="fact")` with a subject
   and content only. The facts carry no `sources`.
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
  temperature 0, with the reference prompts. The baseline extractor stays
  `gpt-4o-mini-2024-07-18`. Phase 7 tries at most one other extractor model, chosen by the
  owner.
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

## Phase 7. The extractor

Goal: the extractor stores the facts that the questions ask about. The target is the
group of misses where no stored fact held the answer: 19 on sample A and 16 on sample B.

Phase 6 built the commands that this phase uses. `--extractor <name>` runs a variant
from `VARIANTS` in `bench/prompts.py`. `bench/diagnose.py --result <file>` gives the
groups of misses of any result file. `--replay` and `--workers N` are described in
`bench/README.md`. A new prompt misses the reply cache, so each variant pays for its
capture, reader, and judge calls. The full-context and retrieval arms do not change, so
run each variant with `--arms memory`.

The library does not change in this phase. All work is in `bench/`.

Tasks:

1. Read the 19 misses of sample A with their sessions, and list what the extractor
   dropped in each. Phase 5 found that it kept the interest of the user and dropped what
   the assistant said: a number, a name, or an item of a list. Make sure that the data
   agrees with this before you change the prompt.
2. Look for three faults in the extractor replies:
   - A reply that reached the limit of 4,000 output tokens.
   - A reply that failed to parse.
   - A fact that the extractor left out because a near fact was in "Facts already in
     memory".

   Read from `bench/models.py` and `bench/e2e.py`, not observed: a reply that reaches the
   limit raises before the cache stores it. A reply that fails to parse is stored in the
   cache, but it stops the run. So no completed result file holds either fault, and this
   task makes sure that this is true.
3. Write a new prompt variant with these rules:
   - If the user can ask later about what the assistant said, keep it.
   - Keep each item of a list as its own fact.
   - Write each date as an absolute date.
   - Keep names and numbers exactly.

   Each variant gets a name, and the old prompt stays as the baseline variant.
4. Each fact carries the source `session:<id>`, as each episode of the retrieval arm
   does. This prepares phase 8, and it lets the diagnosis name the session of a fact.
5. Run each variant on sample A, from the session cache where the prompt did not change.
   Report accuracy, the diagnosis, the facts written per session, and the cost.
6. If the owner names one other extractor model, try it with the best prompt.
7. Run the best variant once on sample B.
8. Record the variants and the result in `docs/benchmarks.md`.

Acceptance criteria:

- The group "no stored fact held the answer" is smaller on sample A and on sample B than
  in their baselines.
- `docs/benchmarks.md` reports every variant tried, with its prompt name, model, and cost.

Stop and ask if:

- No variant reduces the group of 19 by at least a third on sample A.
- A variant needs a field that `Fact` does not have.
- A variant writes more than three times the facts of the baseline. The block then fills
  faster, and the owner decides the trade.

What tasks 1 and 2 found, observed on 2026-10-14 by a replay of `capture` from the cache
of the sample A run:

- The phase 5 finding holds for 5 of the 19 misses. Each of the 5 is of the type
  single-session-assistant, and the answer is a value that the assistant said. The
  baseline prompt already asks for such facts.
- The other 14 misses are a fact that the user stated in passing, in a turn about another
  topic, such as "By the way, I just repainted my bedroom walls a lighter shade of gray".
- The first evidence turn is turn 0 or turn 2 in 21 of the 26 evidence sessions. The
  extractor wrote 126 facts from these sessions, and about 21 percent of them come from
  the first third of the session. A word-overlap match between each fact and each turn
  gave this share, so it is approximate.
- Task 2 found no fault. No cached extractor reply fails to parse, and the largest has
  2,602 output tokens, under the limit of 4,000. No list of "Facts already in memory" for
  the 26 evidence sessions holds a fact near the answer.

Decisions taken during the work. The owner can change any of them in review.

- The variant `detail` adds the rules of task 3 to the baseline prompt. It also tells the
  extractor to read every turn, and to keep a fact that the user states in passing.
- The source of a fact is `session:` and the first 12 hex digits of the SHA-256 of the
  session id, not `session:<id>` (agreed with the owner on 2026-10-14). The block shows
  the sources to the reader, and LongMemEval names each evidence session `answer_...`.
  The plain id tells the reader which facts hold the answer. The diagnosis maps the
  source back to the session id. The episodes of the retrieval arm keep `session:<id>`,
  because the reader does not see their sources.
- The sources make each line of the block longer and change the reader prompt. They
  also change the extractor prompt. `capture` gets "Facts already in memory" from
  `recall` with its default budget of 2,000 characters, and a longer line lets fewer
  facts fit (observed: 9 in place of 10 for question `66f24dbb`). So the extractor cache
  of phase 6 does not replay with sources. The baseline runs again with sources, at the
  full price, as the comparison point of this phase. `--no-fact-sources` runs the arm as phases 5 and 6 did, and a
  result file records `fact_sources`.

## Phase 8. Episodes behind facts

Goal: `recall` can give the reader the source text of its facts. The retrieval arm shows
that whole sessions answer 45 of 54. This phase lets the memory contract use them, within
a budget.

This phase changes the library.

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
2. Build the agreed surface in `Memory`, for both drivers. The conformance suite, the
   ledger in `docs/guarantees.md`, the skill commands, and `CHANGELOG.md` follow.
3. The memory arm captures each session with an episode key. Then it recalls with each
   `episodes` value, at three budgets: 8,000, 32,000, and 64,000 characters.
4. Report accuracy against reader tokens for each value on sample A, then the best one
   on sample B.

Acceptance criteria:

- `MemoryStore` and `HotdataStore` return the same block for the same calls, in the
  conformance suite.
- A capture with an episode writes the episode and the facts in one flush per table, and
  a retried capture writes nothing new.
- `docs/benchmarks.md` reports accuracy and reader tokens for each value and budget.

Stop and ask if:

- The surface needs a new `Store` method or a new record field.
- The best value needs more reader tokens than the retrieval arm, 29,878 at the median.
  Then memory costs as much as retrieval, and the owner decides whether the trade holds.

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

- Phase 7: name a second extractor model to try, or none.
- Phase 8: agree the budgets to try, or change them from 8,000, 32,000, and 64,000
  characters.
- Phase 13: the first version number, and whether to publish to TestPyPI first.
