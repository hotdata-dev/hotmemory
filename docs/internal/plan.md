# Plan: phase 5, a public benchmark

Status: agreed, 2026-10-10. This file
holds the current phase only. The next phase replaces it. The phases themselves are in
`roadmap.md`. Phase 4 closed with PR #11 and the consumer's pull request. `Memory.load`,
the tuple `Filter.kind`, and the episode rule are on `main`.

## Goal

When this phase closes, hotmemory has its first public numbers on LongMemEval-S, a
benchmark of 500 questions over long chat histories. The numbers come in two parts, in
this order.

1. Retrieval recall at k. This score needs no model except the embedder. It compares the
   sessions that hotmemory ranks for a question with the sessions that hold the answer.
   It measures the store and the retrieval query alone.
2. End-to-end accuracy on a sample of the questions. An extractor writes facts, `recall`
   gives a block to a reader model, and a judge model grades the answer. The same sample
   also runs without memory, so the number has a baseline next to it.

`docs/benchmarks.md` holds the numbers. Each number has the configuration that produced
it. The survey (section 2.8) names this run as the proof that hotmemory is a general
memory and not only the feature of one consumer.

## Decisions

The owner agreed to all of these on 2026-10-10, and asked to keep the phase simple and
stay with this plan.

- The benchmark (agreed). LongMemEval-S, from the cleaned data file of 2025-09, which is
  under MIT. LoCoMo, MemoryBench, and DolphinBench wait, for the reasons in the research
  notes of 2026-10-08. The roadmap lists them under Deferred.
- The runner (agreed). The runner lives in `bench/` at the root of the repository,
  outside the package. Its dependencies are in a `bench` dependency group, so the library
  gains no runtime dependency. The data downloads to a folder that git ignores. Ruff and
  mypy check `bench/`. Its offline tests use a small fixture in the shape of the data, and
  `make verify` runs them, so it stays under five seconds. No benchmark run is in CI.
- The retrieval score (agreed). One episode for each session, with the user turns of
  that session as its content, as in the session-level retrieval of the reference code.
  The episode key is the session id, with `-` for each character outside
  `[A-Za-z0-9_-]`. The episode keeps the session id in `sources`. A search with
  `Filter(kind="episode")` and k of 50 gives a ranked list of sessions. The runner scores
  `recall_any@k`, `recall_all@k`, and `ndcg_any@k`, with the formulas of the reference
  code. k is 1, 3, 5, 10, 30, and 50. It scores the questions that the reference code
  scores: not the abstention questions, and not the questions with no evidence in a user
  turn. The research notes count 419 such questions from the data. Task 3 counts them
  again with the reference rules.
- One question at a time (agreed). Each question gets its own namespace. The runner
  loads its sessions, searches, and then deletes its rows before the next question. Thus
  the tables hold one question at a time, as the reference code builds one index for each
  question. This matters for the fused ranking. BM25 takes its top rows from the whole
  table, and the scope filter applies after that. With 500 questions in one table, BM25
  finds almost no row in the scope of one question. A second run keeps every question in
  one table, and the report shows the difference as a known limit of the fused ranking.
- The rankings and the drivers (agreed). The main numbers come from `HotdataStore` on
  the local stack, for both `ranking="fused"` and `ranking="vector"`. `MemoryStore` runs
  the first 20 questions with the same embedder. Its vector ranking must give the same
  sessions in the same order as the vector ranking of `HotdataStore`. The oracle test
  already asks this.
- The embedder (agreed). `OpenAIEmbedder` with `text-embedding-3-small`, through a cache
  on disk that git ignores. The cache key is the model name and the text, so a second run
  calls no model, and the runs can be repeated.
- Where it runs (agreed). The ingest and the scores run on the local stack. A separate
  run of 20 questions on a cloud database measures the latency of `search` at p50, p95,
  and p99. It reports the first call to a cold engine as its own number. The report names
  the setup of every number, local or cloud.
- The end-to-end sample (agreed). 9 questions of each of the 6 question types, 54 in all,
  chosen with a fixed seed. The list of question ids is committed, so every arm and every
  later run uses the same questions. A full run of 500 questions waits for the owner's yes,
  because its cost is about ten times higher.
- The end-to-end arms (agreed). Three arms run on the sample, with the same reader and
  judge.
  1. Full context: the reader gets every session of the question. This is the baseline
     that the paper reports, about 60 percent with GPT-4o.
  2. Retrieval: the reader gets the top sessions that the retrieval score ranks. This arm
     needs no extractor.
  3. Memory: an extractor runs `capture` on each session, in date order, and the reader
     gets the block of `recall`.
- The models (agreed). The reader is GPT-4o and the judge is `gpt-4o-2024-08-06` at
  temperature 0, with the judge prompts of the reference code for each question type. Then
  the numbers compare with the published GPT-4o numbers. The benchmark sets the judge: its
  scoring script takes GPT-4o, GPT-4o mini, or a Llama model, and its README runs it with
  GPT-4o. The benchmark leaves the reader to the caller, and its generation script runs
  readers from GPT-4o to small open models. So GPT-4o as the reader is our choice, made so
  that the numbers compare with the published ones. The extractor is Claude Haiku 4.5.
  The runner reads `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` from the environment. (Changed
  during the work: see the extractor under "Decisions taken during the work".)
- The time rules (agreed). The runner sorts the sessions of a question by date before it
  loads them, because 211 haystacks are out of order. The session date becomes
  `observed_at` in UTC. `recall` takes `as_of` at 23:59:59 on the date of the question,
  because 76 questions have evidence later on the same day. These counts come from the
  research notes and are not observed yet. Task 2 counts them again.
- The cost limit (agreed). The runner prints an estimate before a paid run, and stops if
  the estimate is over 50 dollars. The sample with the three arms is about 25 to 35
  dollars by the research estimates. That estimate is not observed. By the same estimate,
  the full-context arm is about half of it, because each of its 54 reads holds about
  100,000 tokens.

## Tasks

Worked in order on one branch, in one pull request.

1. Close phase 4 in `roadmap.md` and replace `plan.md` with the phase 5 plan.
2. `bench/`: the dependency group, the data download and its checks, the sort by date, the
   time rules, the session ids in keys and sources, and the embedder cache. Offline tests
   on a small fixture.
3. The retrieval metrics, ported from the reference code, with offline tests that compare
   them with values worked by hand.
4. The retrieval runner on the local stack: one question at a time, both rankings, all
   scored questions. Then the run with every question in one table.
5. The driver check: the first 20 questions on `MemoryStore` and on `HotdataStore`, with
   the vector ranking.
6. The cloud latency run of 20 questions.
7. The end-to-end runner: the extractor, the reader, the judge, the three arms, and the
   cost limit. Then the run on the sample of 54.
8. `docs/benchmarks.md` with every number and its configuration, a link from the README,
   and the docs audit.

## Decisions taken during the work

The plan left these details open. The work took the defaults below, and the owner can
change any of them in review.

- The data file is pinned to revision `98d7416c` of the dataset on Hugging Face, with the
  SHA-256 `d6f21ea9...a3c442`. The runner downloads it with the standard library, so the
  `bench` group needs no Hugging Face package. Each load checks the hash.
- Task 2 counted the data again with the loader (observed): 500 questions, 419 scored,
  211 haystacks out of date order, and 76 questions with a session later on the day of
  the question, by up to 23.7 hours. In 43 of the 76, that late session is evidence by
  the rule below. The research notes said 75 of 76, and that count is wrong.
- A session is evidence by the rule of the reference code: its id holds `answer`, and a
  user turn has the answer. In 32 scored questions, `answer_session_ids` also names a
  session with no such user turn. The reference code renames those sessions to `noans`
  and does not count them, and the runner does the same.
- 13 haystacks repeat a session id, always with the same content, and never as evidence.
  The runner stores one episode with the first date. 71 sessions have no user text, and
  none is evidence. The runner stores no episode for them, because a record needs
  content.
- Four distinct session texts are longer than 8,191 tokens, the input limit of the
  embedder, and none is evidence. The embedder cuts each text to 8,191 tokens of
  `cl100k_base` before it embeds it. BM25 still reads the whole text. The cache key is the
  model and the cut text, and the cache keeps 32-bit vectors in a SQLite file.
- An episode has the source `session:<id>`, and the namespace of a question is
  `("bench", "lme_s", <run>, <question id>)`.
- The metrics take a ranked list of session ids and the set of evidence ids. The ideal
  ranking of `ndcg_any@k` puts every evidence id first, as the reference code does when
  it sorts the relevance of the whole haystack. A check outside the repository ran the
  reference `evaluate_retrieval` and the port on 2,000 random rankings, and the largest
  difference was 2.2e-16 (observed).
- The extractor is `gpt-4o-mini-2024-07-18` and not Claude Haiku 4.5. The owner chose
  this on 2026-10-10, because no Anthropic key is available. LongMemEval has no extractor,
  so the benchmark sets nothing here. The reader and the judge stay `gpt-4o-2024-08-06`.
  The chat calls read their key from `OPENAI_MODEL_KEY`, and the embedder keeps
  `OPENAI_API_KEY`. The `bench` group needs no Anthropic package.
- Git ignores `bench/results/`, and `docs/benchmarks.md` records the reported numbers. A
  retrieval file is about 2.2 MB, because it keeps the 50 ranked sessions of each
  question. The owner chose this on 2026-10-12.
- Task 4 (observed, local stack): the shared-table run did not show the loss that the plan
  expected. With 19,864 episodes in one table, the fused ranking scored `recall_any@5`
  0.9761, against 0.9690 with one question at a time. The vector ranking gave the same
  sessions in the same order in both runs. On this data the vector ranking scores higher
  than the fused ranking, for example `recall_all@5` 0.9475 against 0.9045.
- Task 5 (observed): on the first 20 scored questions, `MemoryStore` and `HotdataStore`
  ranked the same sessions in the same order, with distances within 1.0e-6.
- Task 6 (observed, cloud, 20 questions, run by the owner): search p50 998 ms fused and
  361 ms vector. With 19 searches after the first, p95 and p99 are both the slowest search,
  so the report gives the highest value and does not name it p99. The first request of the
  run took 17.7 s, and the report names it the first request, because the file does not
  prove that the engine was cold. The rankings equal those of the local run. No write
  needed a retry.
- The end-to-end runner takes these defaults. The sample comes from all 500 questions with
  the seed 20261010, so 2 of the 54 are abstention questions. The retrieval arm gives the
  reader the top 10 sessions of the fused search, with both roles. The memory arm recalls
  with k of 30 and a budget of 8,000 characters. The full arm gives every session, where the
  reference code keeps the last 50. The reader prompt is the reference prompt with chain of
  thought and the JSON history. The memory arm uses the reference prompt for extracted
  facts. The extractor prompt is new, in `bench/prompts.py`. Each reply is cached on disk.
- Task 7 (observed, local stack, the sample of 54): full context 39/54 (0.722), retrieval
  45/54 (0.833), memory 19/54 (0.352). The run spent $20.95 against an estimate of $24.33,
  in 2,802 model calls. Each recalled block was 4,000 to 5,000 characters, so k of 30
  bounded it and not the budget.
- The diagnosis of the 35 memory misses (`bench/diagnose.py`, observed) replays `capture`
  from the cached replies, and GPT-4o marks the stored facts that state the answer or a
  value that it needs. 19 misses had no such fact: the extractor did not write it. 2 had
  it ranked below the top 30, and the vector ranking ranked it higher in both. 14 had at
  least one marked fact in the top 30. That last count is an upper bound, because a
  multi-session question needs several facts and the marker accepts one, and a few marks
  are loose. A first pass with gpt-4o-mini as the marker counted facts on the same topic,
  and its result was discarded.
- Task 8: `docs/contracts.md` said that a cloud request costs about 400 ms at every size,
  from simple queries in phase 0. It now adds the benchmark numbers: a fused search took
  998 ms at p50 in the cloud, and a vector search 361 ms. No behavior and no frozen surface
  changed in this phase, so `docs/guarantees.md` and `CHANGELOG.md` do not change.
- The branch was rebased onto `main` after PR #12 merged. Each rebased commit has the same
  file tree as the commit that ran, so `hotmemory_commit` in each result file names the
  rebased commit.
- The `dev` group includes the `bench` group, so `make verify` checks `bench/` with the
  same commands. The offline tests of `bench/` are in `bench/tests/`.

## Acceptance criteria

- AC1. `make verify` passes offline in under five seconds, with the offline tests of
  `bench/`.
- AC2. One command runs the retrieval score on the local stack, and `docs/benchmarks.md`
  reports `recall_any@k`, `recall_all@k`, and `ndcg_any@k` for every scored question, for
  both rankings.
- AC3. On the first 20 questions, the vector ranking of `MemoryStore` and of
  `HotdataStore` give the same sessions in the same order.
- AC4. `docs/benchmarks.md` reports the accuracy of the three arms on the same 54
  questions, for each question type and in total, with the reader, the judge, and the
  extractor named.
- AC5. Every number in `docs/benchmarks.md` was observed in this phase, and its line names
  the configuration: the data file hash, the models, the embedder, k, the budget, the
  driver, and local or cloud.
- AC6. No public text names a private repository, a customer, or a deployment detail. A
  grep for the known names proves it over the files, the commit messages, the pull
  request, and the issue.

## Verification

- Per commit: `make verify`.
- Per commit that touches a driver: `make local-up`, then `make integration`.
- Each run writes its configuration and its results to one JSON file. The numbers in
  `docs/benchmarks.md` come from those files.
- Before the pull request: the docs audit of the `pr-workflow` skill, and the grep for
  AC6.

## Questions left open

- A search over history, so that `recall(as_of=T)` can return a fact superseded after T.
  Some temporal questions ask for a value before it changed. The report shows the score of
  each question type, so the cost of this gap becomes visible.
- After the engine fixes the filtered vector index search: let the vector rankings use
  the index again, and measure the gain.
- BM25 in a narrow scope. If the shared-table run shows a large loss, a later phase can
  rank BM25 inside the scope.
- An exact count for each namespace in the block of `profile`.
- The ideas from the study of another memory layer on 2026-10-09 wait until this roadmap
  reaches its basic memory layer.

## Stop and ask if

- A benchmark needs a field that the record does not have, or a new `Store` method.
- The ingest of the local run takes more than two hours.
- The cost estimate of a paid run is over 50 dollars.
- The retrieval score of the two drivers differs on the first 20 questions.
- The reference code and the research notes disagree on the scored questions or on a
  formula.
