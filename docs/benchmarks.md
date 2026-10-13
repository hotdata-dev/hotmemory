# Benchmarks

This page reports the numbers of hotmemory on LongMemEval-S, a public benchmark of 500
questions over long chat histories. Each number on this page was observed on 2026-10-10.
Each section names the result file that a run wrote into `bench/results/`, with its
numbers, its configuration, and the commit of hotmemory that ran it. Git ignores that
folder, so this page is the record of the numbers. [bench/README.md](../bench/README.md)
gives the commands that repeat each run and write the files again.

## The data and the common configuration

| Item | Value |
|---|---|
| Data file | `longmemeval_s_cleaned.json`, the cleaned release of 2025-09, revision `98d7416c` of the dataset on Hugging Face |
| SHA-256 of the data file | `d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442` |
| Embedder | OpenAI `text-embedding-3-small`, 1,536 dimensions. Each text is cut to 8,191 tokens. |
| Driver | `HotdataStore`, except in the driver check |
| Engine, local | The local stack of [docs/local.md](local.md), RuntimeDB image `sha256:48046ae3bf22`, hotdata-framework 0.14.1 |

Each question has its own history of 38 to 62 sessions. The runner sorts the sessions of a
question by date, because 211 of the 500 histories are out of order in the file. Each
session date becomes `observed_at` in UTC.

## Retrieval

The retrieval score needs no model except the embedder. For each question, the runner
stores one episode for each session, with the user turns of that session as its content.
Then it searches with the question for the 50 closest episodes, with
`Filter(kind="episode")`. It compares the ranked sessions with the evidence sessions.

The metrics and the scored questions are those of the reference code of LongMemEval. For
the reference code, an evidence session has `answer` in its id and the answer in one of
its user turns. The
score leaves out the 30 abstention questions and the 51 questions with no evidence in a
user turn, so 419 questions are scored.

- `recall_any@k`: when one evidence session is in the first k, the value is 1.
- `recall_all@k`: when every evidence session is in the first k, the value is 1.
- `ndcg_any@k` is the normalized discounted cumulative gain of the first k.

Each value below is the mean over the 419 scored questions. The run used the local stack,
with one question at a time in the tables. File:
`retrieval-hotdata-local-isolated-fused-vector.json`.

| Ranking | Metric | k=1 | k=3 | k=5 | k=10 | k=30 | k=50 |
|---|---|---|---|---|---|---|---|
| fused | `recall_any` | 0.8807 | 0.9594 | 0.9690 | 0.9928 | 1.0000 | 1.0000 |
| fused | `recall_all` | 0.2458 | 0.8329 | 0.9045 | 0.9570 | 1.0000 | 1.0000 |
| fused | `ndcg_any` | 0.8807 | 0.9067 | 0.9181 | 0.9311 | 0.9367 | 0.9367 |
| vector | `recall_any` | 0.8783 | 0.9666 | 0.9833 | 1.0000 | 1.0000 | 1.0000 |
| vector | `recall_all` | 0.2530 | 0.8592 | 0.9475 | 0.9928 | 0.9976 | 1.0000 |
| vector | `ndcg_any` | 0.8783 | 0.9247 | 0.9375 | 0.9479 | 0.9483 | 0.9485 |

`ranking="fused"` fuses BM25, the content vector, and the cue vector.
`ranking="vector"` ranks by the content vector alone. On this data, the vector ranking
scores higher than the fused ranking at k of 3 and more. The reason was not measured.

The same run, by question type:

| Question type | Questions | fused `recall_all@5` | vector `recall_all@5` | fused `recall_all@10` | vector `recall_all@10` |
|---|---|---|---|---|---|
| single-session-user | 64 | 1.0000 | 0.9688 | 1.0000 | 1.0000 |
| single-session-assistant | 5 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| single-session-preference | 30 | 0.8333 | 1.0000 | 0.9333 | 1.0000 |
| multi-session | 121 | 0.8430 | 0.9421 | 0.9256 | 0.9835 |
| knowledge-update | 72 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| temporal-reasoning | 127 | 0.8740 | 0.8976 | 0.9449 | 0.9921 |

The LongMemEval paper reports retrieval only on its larger M split, so no published
number on S compares with this table.

### Every question in one table

A second run kept the episodes of all 419 questions in the same tables, 19,864 rows, and
searched each question inside its own namespace. BM25 in the fused ranking takes its top
rows from the whole table before the namespace filter applies, so this run tests that
limit. On the local stack, the fused ranking scored higher at the top 5 and slightly lower
at the top 10. At the top 10, 1 more question had no evidence session. The vector ranking
gave the same sessions in the same order in both runs. File:
`retrieval-hotdata-local-shared-fused-vector.json`.

| Ranking | Tables hold | `recall_any@5` | `recall_all@5` | `recall_any@10` | `recall_all@10` | `ndcg_any@10` |
|---|---|---|---|---|---|---|
| fused | one question | 0.9690 | 0.9045 | 0.9928 | 0.9570 | 0.9311 |
| vector | one question | 0.9833 | 0.9475 | 1.0000 | 0.9928 | 0.9479 |
| fused | every question | 0.9761 | 0.9165 | 0.9905 | 0.9499 | 0.9370 |
| vector | every question | 0.9833 | 0.9475 | 1.0000 | 0.9928 | 0.9479 |

### The driver check

On the first 20 scored questions, the vector ranking of `MemoryStore` and that of
`HotdataStore` gave the same sessions in the same order. The largest difference between
two distances was 1.0e-6. `MemoryStore` ran in process with a fixed clock. File:
`retrieval-memory-in-process-isolated-vector-first20.json`, compared with
`bench/compare.py`.

## Latency

Each search time below includes the request to the engine and excludes the embedding of
the question, which came from the cache. Each write stores the episodes of one question in
one flush. Each delete removes them.

On the local stack, over the 419 questions of the retrieval run, with one question at a
time:

| Operation | p50 | p95 | p99 | Highest |
|---|---|---|---|---|
| Search, fused | 130 ms | 205 ms | 273 ms | 517 ms |
| Search, vector | 42 ms | 65 ms | 95 ms | 138 ms |
| Write of one question | 0.29 s | 0.49 s | 0.62 s | 1.30 s |
| Delete of one question | 0.22 s | 0.38 s | 0.52 s | 1.05 s |

In the run with every question in one table, a write took 1.91 s at p50. Each flush
reads the current rows of a table that grows.

On a cloud workspace, over the first 20 scored questions, with one question at a time.
File: `retrieval-hotdata-cloud-isolated-fused-vector-first20.json`.

| Operation | p50 | Highest |
|---|---|---|
| Search, fused | 998 ms | 1,675 ms |
| Search, vector | 361 ms | 1,068 ms |
| Write of one question | 2.77 s | 3.98 s |
| Delete of one question | 2.29 s | 2.94 s |

The cloud table gives no p95 or p99, because 19 searches after the first are too few for
them. The first request of the cloud run took 17.7 s. The file does not prove that the
engine was cold before it, so this page does not call it a cold start. The first search
took 1,053 ms fused and 366 ms vector. The cloud run ranked the same sessions in the same
order as the local run.

## End-to-end accuracy

Three arms answer the same 54 questions, 9 of each question type, with the same reader and
the same judge. A fixed seed chose the questions, and their ids are in
[bench/sample.json](../bench/sample.json). 2 of the 54 are abstention questions. File:
`e2e-sample.json`.

| Item | Value |
|---|---|
| Reader | `gpt-4o-2024-08-06`, temperature 0, up to 800 output tokens, the chain-of-thought prompt of the reference code |
| Judge | `gpt-4o-2024-08-06`, temperature 0, the prompt of the reference code for each question type |
| Extractor of the memory arm | `gpt-4o-mini-2024-07-18`, with the prompt in `bench/prompts.py` |
| Driver | `HotdataStore` with the fused ranking, on the local stack |

The arms:

1. Full context: the reader gets every session of the question.
2. Retrieval: the reader gets the 10 sessions that the fused search ranks first, with both
   roles.
3. Memory: `capture` runs on each session, in date order. Then `recall` gives the reader
   its block, with k of 30, a budget of 8,000 characters, and `as_of` at 23:59:59 on the
   date of the question.

| Question type | Questions | Full context | Retrieval | Memory |
|---|---|---|---|---|
| single-session-user | 9 | 7 | 8 | 3 |
| single-session-assistant | 9 | 9 | 8 | 3 |
| single-session-preference | 9 | 8 | 8 | 3 |
| multi-session | 9 | 4 | 6 | 3 |
| knowledge-update | 9 | 5 | 7 | 4 |
| temporal-reasoning | 9 | 6 | 8 | 3 |
| All | 54 | 39 (72.2%) | 45 (83.3%) | 19 (35.2%) |

At the median, the reader read 113,689 tokens in the full-context arm, 29,878 in the
retrieval arm, and 1,462 in the memory arm. The run made 2,802 model calls and cost
$20.95.

The LongMemEval paper reports 60.6% for full context with GPT-4o on all 500 questions of
the earlier data file. A sample of 54 questions and the cleaned data file do not compare
with that number closely.

### Why the memory arm scored low

`bench/diagnose.py` ran `capture` again on each of the 35 questions that the memory arm
missed, from the cached extractor replies. GPT-4o then marked each stored fact that states
the answer or a value that the answer needs, and the search ranked every fact. File:
`e2e-sample-diagnosis.json`.

| Cause | Questions |
|---|---|
| No stored fact states the answer or a value that it needs | 19 |
| A stored fact holds it, and the fused search ranked it below 30 | 2 |
| At least one marked fact was in the top 30 | 14 |

The extractor is the main loss. Most often it stored the interest of the user. It dropped
what the assistant said, for example a number, a name, or an item of a list. In both
questions of the second row, the vector ranking ranked the fact higher than the fused
ranking did. The last row is an upper bound on the misses of the reader. A multi-session
question needs several facts. The marker counts the question when it finds one of them in
the top 30.

## Known limits

- The end-to-end numbers come from a sample of 54 questions, so one question moves an
  arm by 1.9 points. A run on all 500 questions waits for the owner's agreement.
- The full-context arm gives the reader every session. The reference code keeps the last
  50 sessions, so the two differ for histories longer than 50 sessions.
- The extractor of the memory arm is the only one tried. A stronger extractor or another
  prompt was not measured.
- `recall` keeps the records that are valid at `as_of`, and a search over history does not
  exist yet. A question about a value before it changed can need a superseded record.
