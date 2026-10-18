# Benchmarks

This page reports the numbers of hotmemory on LongMemEval-S, a public benchmark of 500
questions over long chat histories. Each number on this page was observed on 2026-10-10,
except where a section gives another date. Each section names the result file that a run wrote into `bench/results/`, with its
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
scores higher than the fused ranking at k of 3 and more. The section on the ablation below
gives the reason.

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

### The ablation of the fused ranking

Phase 10 ranked each question with the fused query less one arm at a time, with
`--ablate`. The episodes have no cues, so the cue arm is empty. The fused query is thus
BM25 and the content vector. Observed on 2026-10-16, on the local stack, with one question at
a time in the tables, at commit `6a0fb70` with the changes of `--ablate`. The embedder took
every vector from the cache. File:
`retrieval-hotdata-local-isolated-fused-vector-bm25-content-content-cue-bm25.json`.

| Ranking | Arms | `recall_any@5` | `recall_all@5` | `recall_all@10` | `ndcg_any@10` | Search p50 |
|---|---|---|---|---|---|---|
| `fused` | BM25, content, cue | 0.9690 | 0.9045 | 0.9570 | 0.9311 | 160 ms |
| `bm25-content` | BM25, content | 0.9690 | 0.9045 | 0.9570 | 0.9311 | 111 ms |
| `content-cue` | content, cue | 0.9833 | 0.9475 | 0.9928 | 0.9479 | 126 ms |
| `bm25` | BM25 | 0.9475 | 0.8234 | 0.8926 | 0.8766 | 86 ms |
| `vector` | the content vector, by distance | 0.9833 | 0.9475 | 0.9928 | 0.9479 | 49 ms |

`bm25-content` ranked the same sessions as `fused` on 419 of 419 questions.
`content-cue` ranked the same top 10 as `vector` on 419 of 419 questions. Thus the BM25
arm makes the whole gap between the two rankings.

By question type, at `recall_all@10`:

| Question type | Questions | `fused` | `vector` | `bm25` |
|---|---|---|---|---|
| single-session-user | 64 | 1.0000 | 1.0000 | 1.0000 |
| single-session-assistant | 5 | 1.0000 | 1.0000 | 1.0000 |
| single-session-preference | 30 | 0.9333 | 1.0000 | 0.7667 |
| multi-session | 121 | 0.9256 | 0.9835 | 0.8182 |
| knowledge-update | 72 | 1.0000 | 1.0000 | 0.9861 |
| temporal-reasoning | 127 | 0.9449 | 0.9921 | 0.8819 |

On 16 questions, `fused` lost an evidence session from the top 10 that `vector` had: 8
multi-session, 6 temporal-reasoning, and 2 single-session-preference. BM25 ranked each lost
session from 23 to 40. Fusion with equal weights thus pushed it below sessions that BM25
ranked high. The run holds about 50 sessions for each question, and BM25 fetches 500 rows,
so the filter after BM25 did not cause the loss.

BM25 favors long sessions a little. In each haystack, a session has a length percentile
from 0, the shortest, to 1, the longest. The sessions that are not evidence and that
`fused` adds to the top 10 have a mean percentile of 0.645. Those in the top 10 of
`vector` have 0.583, and the evidence sessions have 0.672.

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

Three arms answer the same questions, with the same reader and the same judge. Each run
uses one of two samples of 54 questions, 9 of each question type. A fixed seed chose each
sample.

| Sample | File of ids | Seed | Abstention questions | Use |
|---|---|---|---|---|
| A | [bench/sample.json](../bench/sample.json) | `20261010` | 2 | Each phase iterates on it. |
| B | [bench/sample-b.json](../bench/sample-b.json) | `20261013` | 4 | Each phase runs it once, at its end. It holds no question of sample A. |

| Item | Value |
|---|---|
| Reader | `gpt-4o-2024-08-06`, temperature 0, up to 800 output tokens, the chain-of-thought prompt of the reference code |
| Judge | `gpt-4o-2024-08-06`, temperature 0, the prompt of the reference code for each question type |
| Extractor of the memory arm | `gpt-4o-mini-2024-07-18`, with the `baseline` prompt in `bench/prompts.py`, except in the section on extractor variants |
| Driver | `HotdataStore` on the local stack, with the fused ranking up to phase 9. Each phase 10 section names its ranking. |

The arms:

1. Full context: the reader gets every session of the question.
2. Retrieval: the reader gets the 10 sessions that the fused search ranks first, with both
   roles.
3. Memory: `capture` runs on each session, in date order. Then `recall` gives the reader
   its block, with k of 30, a budget of 8,000 characters, and `as_of` at 23:59:59 on the
   date of the question.

### Sample A

File: `e2e-sample.json`.

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

On 2026-10-13, the memory arm ran again on sample A with `--replay`, from the cached
replies, at commit `ce97385` with uncommitted changes to documentation only. It gave the same answer and the same mark on each of the 54
questions, with no model call and no embedder call. File: `e2e-sample-a.json`. On
2026-10-14, the same replay with `--workers 4` at commit `4d99fbe` gave the same 54 rows
again. It took 519 seconds, and the replay with one process took 2,264 seconds. File:
`e2e-sample-a-workers4.json`.

The LongMemEval paper reports 60.6% for full context with GPT-4o on all 500 questions of
the earlier data file. A sample of 54 questions and the cleaned data file do not compare
with that number closely.

### Sample B

Observed on 2026-10-14, at commit `5f2abb9`. File: `e2e-sample-b.json`.

| Question type | Questions | Full context | Retrieval | Memory |
|---|---|---|---|---|
| single-session-user | 9 | 8 | 9 | 6 |
| single-session-assistant | 9 | 9 | 9 | 4 |
| single-session-preference | 9 | 3 | 6 | 0 |
| multi-session | 9 | 7 | 7 | 5 |
| knowledge-update | 9 | 8 | 8 | 5 |
| temporal-reasoning | 9 | 8 | 9 | 7 |
| All | 54 | 43 (79.6%) | 48 (88.9%) | 27 (50.0%) |

At the median, the reader read 113,314 tokens in the full-context arm, 30,899 in the
retrieval arm, and 1,466 in the memory arm. The run made 2,869 model calls, cost $21.42,
and took 2.9 hours with one process.

The memory arm scored 8 questions more on sample B than on sample A, with the same
configuration. That is 14.8 points. A gain on one sample of 54 questions is therefore
weak evidence, and a change counts only if both samples move the same way.

### Why the memory arm scored low

`bench/diagnose.py` ran `capture` again on each question that the memory arm missed, from
the cached extractor replies. GPT-4o then marked each stored fact that states the answer
or a value that the answer needs, and the search ranked every fact. A second GPT-4o call
then read the marked facts in the block. It told whether they hold every value that the
answer needs. Files: `e2e-sample-a-diagnosis.json`, observed on 2026-10-13,
and `e2e-sample-b-diagnosis.json`, observed on 2026-10-14.

| Cause | Sample A | Sample B |
|---|---|---|
| No stored fact states the answer or a value that it needs | 19 | 16 |
| A stored fact holds it, and the fused search ranked it below 30 | 2 | 0 |
| A marked fact was in the top 30, and `recall` left it out | 0 | 0 |
| The block holds every needed fact, and the reader missed | 1 | 1 |
| The block holds some of the needed facts | 13 | 10 |
| Misses | 35 | 27 |

The three causes of phase 5 (19, 2, and 14 on sample A) are the same in this run, on
each question. The split of the last group is new.

The extractor is the main loss on both samples. On sample A, phase 5 read the misses. Most
often the extractor stored the interest of the user. It dropped what the assistant said, for example a number, a name, or an item of a
list. In both questions of the second row, the vector ranking ranked the fact higher than
the fused ranking did. When the block held a marked fact, it held every needed fact in
only 2 of 25 misses. A question that needs several facts then lacks at least one of them.

The misses of each question type, on sample A:

| Question type | Misses | Not stored | Ranked below 30 | All present | Some present |
|---|---|---|---|---|---|
| single-session-user | 6 | 4 | 1 | 1 | 0 |
| single-session-assistant | 6 | 5 | 0 | 0 | 1 |
| single-session-preference | 6 | 4 | 1 | 0 | 1 |
| multi-session | 6 | 3 | 0 | 0 | 3 |
| knowledge-update | 5 | 2 | 0 | 0 | 3 |
| temporal-reasoning | 6 | 1 | 0 | 0 | 5 |

The misses of each question type, on sample B:

| Question type | Misses | Not stored | Ranked below 30 | All present | Some present |
|---|---|---|---|---|---|
| single-session-user | 3 | 3 | 0 | 0 | 0 |
| single-session-assistant | 5 | 4 | 0 | 0 | 1 |
| single-session-preference | 9 | 6 | 0 | 0 | 3 |
| multi-session | 4 | 1 | 0 | 0 | 3 |
| knowledge-update | 4 | 1 | 0 | 1 | 2 |
| temporal-reasoning | 2 | 1 | 0 | 0 | 1 |

### The extractor variants

Phase 7 tried two extractor prompts, each with `gpt-4o-mini-2024-07-18`, on the memory arm
only. Both are in `bench/prompts.py`.

| Variant | What the prompt asks for |
|---|---|
| `baseline` | The prompt of phases 5 and 6. |
| `detail` | The `baseline` rules, with four more. Read every turn. Keep a fact that the user states in passing. Keep each item of a list as its own fact. Write each date as an absolute date. |

From phase 7, each fact carries the source `session:<hash>`, with the first 12 hex digits
of the SHA-256 of the session id. The block shows the sources to the reader, so each line
is longer than in phases 5 and 6. The runs of phases 5 and 6 had no sources. The baseline
therefore ran again with sources, as the point of comparison. On 2026-10-14, at commit
`db2fe63`, a replay of sample A with `--no-fact-sources` gave the same answer and the same
mark on each of the 54 questions as phase 6, with no model call. File:
`e2e-sample-a-replay-no-sources.json`.

Observed on 2026-10-14, at commit `a9d87c0`, with 4 workers.

| Run | Sample | Correct of 54 | Facts per session | Reader tokens at the median | Sessions at the token limit | Cost | File |
|---|---|---|---|---|---|---|---|
| `baseline`, no sources (phase 6) | A | 19 | 5.34 | 1,462 | not recorded | see above | `e2e-sample-a.json` |
| `baseline` | A | 22 | 5.36 | 1,698 | 0 | at least $0.92 | `e2e-sample-a-baseline-sources.json` |
| `detail` | A | 31 | 5.39 | 1,655 | 1 | at least $1.73 | `e2e-sample-a-detail.json` |
| `baseline`, no sources (phase 6) | B | 27 | 5.20 | 1,466 | not recorded | see above | `e2e-sample-b.json` |
| `detail` | B | 32 | 5.00 | 1,664 | 0 | $1.71 | `e2e-sample-b-detail.json` |

The first try of each run on sample A stopped at question `a3332713`. One extractor reply
reached the limit of 4,000 output tokens on a session where the user pastes a list of
survey answers. From commit `a9d87c0`, such a reply gives no facts for its session, and
the run goes on. The second try took the replies of the first from the cache. The cost of
each run on sample A is the sum of both tries. The first try printed its cost only after
each question, so its last calls are not in the sum.

Correct answers by question type:

| Question type | `baseline`, A | `detail`, A | `baseline` no sources, B | `detail`, B |
|---|---|---|---|---|
| single-session-user | 4 | 4 | 6 | 6 |
| single-session-assistant | 3 | 2 | 4 | 4 |
| single-session-preference | 3 | 4 | 0 | 1 |
| multi-session | 3 | 6 | 5 | 6 |
| knowledge-update | 4 | 8 | 5 | 7 |
| temporal-reasoning | 5 | 7 | 7 | 8 |
| All | 22 | 31 | 27 | 32 |

The diagnosis of each run, with the same commands as above. The diagnoses cost $0.31,
$0.29, and $0.27.

| Cause | `baseline`, A | `detail`, A | `baseline` no sources, B | `detail`, B |
|---|---|---|---|---|
| No stored fact states the answer or a value that it needs | 19 | 16 | 16 | 14 |
| A stored fact holds it, and the fused search ranked it below 30 | 1 | 1 | 0 | 0 |
| A marked fact was in the top 30, and `recall` left it out | 0 | 0 | 0 | 0 |
| The block holds every needed fact, and the reader missed | 0 | 0 | 1 | 1 |
| The block holds some of the needed facts | 12 | 6 | 10 | 7 |
| Misses | 32 | 23 | 27 | 22 |

`detail` gained 13 questions on sample A over the `baseline` with sources, and lost 4. Of
the 13, 9 were misses where the block of the baseline held some of the needed facts. So
most of the gain comes from facts that are more complete, with exact values and absolute
dates. The group with no stored fact got smaller by 3 on sample A and by 2 on sample B.

Phase 7 read the misses and found these facts:

- Phase 5 said that the extractor dropped what the assistant said. On sample A, that
  holds for 5 of the 19 misses with no stored fact. The other 14 are a fact that the user
  states in passing, in a turn about another topic.
- With `detail`, the extractor still drops what the assistant said. On sample A, 7 misses
  of the type single-session-assistant have no stored fact. For 3 of their sessions,
  `detail` wrote no fact at all.
- The marker of the diagnosis can miss facts. On sample A, `detail` stored the facts that
  hold the answer for `2ce6a0f2`, `92a0aa75`, and `71017277`, and the marker marked none of
  them. The marker reads all facts of a question in one call, 226 to 300 facts here. So
  the group with no stored fact can be smaller than the table shows, in each run.

### Episodes behind facts

Phase 8 stored each session as an episode beside its facts, and let `recall` add episodes
to the block after the facts. The runs use the `detail` extractor of phase 7, and the
options `--session-episodes` and `--episodes` of the runner. `capture` stores each session
under the key `session-<hash>`, with the hash of phase 7, and each fact cites that episode.
The block shows the source as `session-<hash>@1`. The facts carry no `session:<hash>`
source. The two values of `episodes`:

| Value | The episodes after the facts |
|---|---|
| `none` | No episode. The block holds the facts only. |
| `cited` | The episodes that the facts of the block cite, in the order of the facts. |
| `search` | The top 30 episodes of a fused search with the question. |

Each block holds whole episodes only, and it stops before the first episode that does not
fit in the budget. `none` was run at 16,000 characters only. With k of 30, its largest
block held 5,344 characters on sample A and 5,626 on sample B, so a larger budget gives
the same block.

Observed on 2026-10-15, at commit `320e891`, with 4 workers.

| Run | Sample | Correct of 54 | Reader tokens at the median | Episodes in the block at the median | Gained and lost against `none` | Cost | File |
|---|---|---|---|---|---|---|---|
| `none`, 16,000 | A | 29 | 1,715 | 0 | | $0.52 | `e2e-sample-a-p8-none-16k.json` |
| `cited`, 16,000 | A | 31 | 1,728 | 0 | +3, -1 | $0.12 | `e2e-sample-a-p8-cited-16k.json` |
| `search`, 16,000 | A | 34 | 1,738 | 0 | +6, -1 | $0.08 | `e2e-sample-a-p8-search-16k.json` |
| `cited`, 32,000 | A | 38 | 6,300 | 2 | +10, -1 | $1.04 | `e2e-sample-a-p8-cited-32k.json` |
| `search`, 32,000 | A | 43 | 5,715 | 2 | +14, -0 | $0.75 | `e2e-sample-a-p8-search-32k.json` |
| `cited`, 64,000 | A | 44 | 12,446 | 4 | +15, -0 | at least $1.01 | `e2e-sample-a-p8-cited-64k.json` |
| `search`, 64,000 | A | 45 | 12,849 | 4 | +17, -1 | $1.93 | `e2e-sample-a-p8-search-64k.json` |
| `none`, 16,000 | B | 31 | 1,723 | 0 | | $0.57 | `e2e-sample-b-p8-none-16k.json` |
| `search`, 64,000 | B | 45 | 12,676 | 4 | +15, -1 | $1.91 | `e2e-sample-b-p8-search-64k.json` |

The retrieval arm read 29,878 tokens at the median on sample A, and scored 45. `search` at
64,000 characters scored 45 on sample A and 45 on sample B, with 12,849 and 12,676 reader
tokens. Sample A and sample B moved the same way, from 29 to 45 and from 31 to 45.

The `none` runs scored 29 on sample A and 31 on sample B, against 31 and 32 with the
`session:<hash>` source of phase 7. The new source changes the line length in the block,
so the list of known facts that `capture` gives the extractor can change. Then the
extractor gets a new prompt and a new reply. The `none` runs are the baseline of this
section.

Each run after the first on a sample replayed `capture` from the extractor cache. A run
that changed only `episodes` or the budget paid for the reader and the judge only. The
first try of the runs with `cited` at 64,000 and every `search` run stopped at an internet
outage, and the next try of `cited` at 64,000 stopped at one engine error. Each later try
took the replies of the earlier tries from the cache. The log of the first try of `cited` at
64,000 was lost, so its cost is not known. The cost of a run is the sum of its tries. The
runs and the diagnoses cost at least $8.10.

Correct answers by question type:

| Question type | A `none` | A `cited` 64,000 | A `search` 32,000 | A `search` 64,000 | B `none` | B `search` 64,000 |
|---|---|---|---|---|---|---|
| single-session-user | 3 | 9 | 9 | 8 | 6 | 9 |
| single-session-assistant | 2 | 6 | 9 | 9 | 2 | 9 |
| single-session-preference | 4 | 7 | 5 | 6 | 1 | 5 |
| multi-session | 6 | 6 | 6 | 6 | 6 | 7 |
| knowledge-update | 8 | 8 | 8 | 8 | 7 | 7 |
| temporal-reasoning | 6 | 8 | 6 | 8 | 9 | 8 |
| All | 29 | 44 | 43 | 45 | 31 | 45 |

The diagnosis of the `none` runs and of `search` at 64,000, with the commands above. The
last row counts the misses whose block held an evidence session as an episode. The
diagnoses cost $0.17 in total.

| Cause | A `none` | A `search` 64,000 | B `none` | B `search` 64,000 |
|---|---|---|---|---|
| No stored fact states the answer or a value that it needs | 17 | 5 | 17 | 5 |
| A stored fact holds it, and the fused search ranked it below 30 | 1 | 0 | 0 | 0 |
| A marked fact was in the top 30, and `recall` left it out | 0 | 0 | 0 | 0 |
| The block holds every needed fact, and the reader missed | 1 | 2 | 0 | 1 |
| The block holds some of the needed facts | 6 | 2 | 6 | 3 |
| Misses | 25 | 9 | 23 | 9 |
| The block held an evidence session as an episode | not run | 8 | not run | 8 |

The two target groups of the phase got smaller on both samples. The misses with no stored
fact went from 17 to 5 on each sample. The misses with some needed facts went from 6 to 2
on sample A and from 6 to 3 on sample B. The `none` runs missed every question of the
type single-session-assistant with no stored fact, 7 on each sample. `search` at 64,000
missed none of that type. In 8 of the 9 misses on each sample, the block held an evidence
session. A question of the type multi-session can need more sessions than the block held.
The type single-session-preference has the most misses with `search` at 64,000: 3 on
sample A and 4 on sample B.

### Faster evaluation

Phase 9 kept the memory of a run and reused it for runs that change only `recall`. The
section "Faster runs" of [bench/README.md](../bench/README.md) gives the options. Each run
below is `--replay` on sample A, with `--extractor detail --session-episodes` and 4
workers, so no run called a model. Observed on 2026-10-16, at commit `630e3be`. The last commit that
changed `src/hotmemory` is `b5443ac`, the same code as the phase 8 runs.

| Run | Correct of 54 | Time | Rows equal to | File |
|---|---|---|---|---|
| `search`, 64,000, `--keep` | 45 | 762.4 s | phase 8 `search` 64,000: 54 of 54 | `e2e-sample-a-p9-keep-search-64k.json` |
| `search`, 32,000, `--reuse` | 43 | 13.0 s | phase 8 `search` 32,000: 54 of 54 | `e2e-sample-a-p9-reuse-search-32k.json` |
| `cited`, 64,000, `--reuse` | 44 | 15.3 s | phase 8 `cited` 64,000: 54 of 54 | `e2e-sample-a-p9-reuse-cited-64k.json` |
| `search`, 64,000, `--reuse` | 45 | 11.1 s | the `--keep` run: 54 of 54, with the same block SHA-256 | `e2e-sample-a-p9-reuse-search-64k.json` |

Two rows are equal in four values: the answer, the verdict, the block length, and the
episodes in the block. The `--keep` run also wrote the same count of facts for each question as
phase 8. The phase 8 runs of the same configurations took 1,133 to 2,085 seconds, and
those times include model calls that were not in the cache. The 54 kept databases hold
192 MB in the storage of the local engine.

The diagnosis of the `--keep` run read the kept databases, and did not run `capture`. It
took 7 seconds and called no model. For each of the 9 misses, it gave the same cause,
marked facts, and evidence check as the phase 8 diagnosis of `search` at 64,000.

The smoke run uses the same options with `search` at 64,000 characters, on the 18
questions of `bench/sample-smoke.json` and `MemoryStore`, in one process. Its first run
paid for the extraction. `MemoryStore` ranks by vector only, so the extractor saw other
lists of known facts than in the runs on the local engine. Observed on
2026-10-16, at commit `298fbe7`:

| Run | Correct of 18 | Time | Cost | Model calls | File |
|---|---|---|---|---|---|
| First, `--run` | 16 | 2,080.0 s | $1.07 | 842 | `e2e-smoke-p9-first.json` |
| Again, `--replay` | 16 | 12.7 s | $0 | 0 | `e2e-smoke-p9-cached.json` |
| Again, `--replay --workers 4` | 16 | 3.8 s | $0 | 0 | `e2e-smoke-p9-cached-w4.json` |

Each replay gave the same answer, verdict, and block SHA-256 for each of the 18 questions.
With 4 workers, each worker holds a `MemoryStore` of its own. A first run with 4 workers
was not measured.
The time in the table is `duration_s` of the result file. The whole command took 563
seconds the first time, and 14.9 seconds on a later try. The cause of the first wait was
not found.

### The ranking end to end

Phase 10 ran the memory arm on sample A with each ranking of `capture` and `recall`, with
`search` at 64,000 characters, `--extractor detail --session-episodes`, and 4 workers.
Observed on 2026-10-16, with `src/hotmemory` at `b5443ac`. The fused run reused the kept
memory of phase 9. The vector run kept its own memory. Its first `--replay` stopped at a
cache miss after 3 questions, and a `--run --resume` ran the other 51.

| Ranking | Correct of 54 | Reader tokens, median | Episodes in the block, mean | Facts written | Cost | File |
|---|---|---|---|---|---|---|
| `fused` | 45 | 12,849 | 4.06 | 13,666 | $0 | `e2e-sample-a-p10-reuse-fused-search-64k.json` |
| `vector` | 46 | 13,405 | 4.48 | 14,322 | $2.17 | `e2e-sample-a-p10-keep-vector-search-64k.json` |

The reader tokens, episodes, and facts of the fused row are those of its `--keep` run,
`e2e-sample-a-p9-keep-search-64k.json`. The two rankings differ on 3 questions. `vector`
answered `66f24dbb`, single-session-user, and `21d02d0d`, multi-session, and missed
`0edc2aef`, single-session-preference. The ranking also changes `capture`, because the
extractor reads the facts that the search of the store returns. Thus the two runs stored
different facts. The vector run took 1,770.6 seconds and 1,722 model calls. In one session,
the extractor reply reached the token limit, and that session gave no facts.

### Episode chunks

Phase 10 stored each session as chunks with `capture`, `chunk_chars`, and ran `search`
with each chunk size at three budgets, on sample A, with the vector ranking,
`--extractor detail`, and 4 workers. Each chunk size was one run with `--keep` at 8,000
characters, and each larger budget was a run with `--reuse`. Observed on 2026-10-18,
with `src/hotmemory` at `12339f8`. The reader tokens are the median over the 54
questions, rounded down, and the chunks are the mean count of chunks in the block.

| Chunk size | Budget | Correct of 54 | Reader tokens | Chunks in the block | Cost | File |
|---|---|---|---|---|---|---|
| 1,000 | 8,000 | 41 | 2,470 | 3.1 | $0.55 | `e2e-sample-a-p10-keep-chunks1000-8k.json` |
| 1,000 | 16,000 | 48 | 4,336 | 12.4 | $0.81 | `e2e-sample-a-p10-reuse-chunks1000-16k.json` |
| 1,000 | 32,000 | 49 | 7,831 | 29.9 | $1.27 | `e2e-sample-a-p10-reuse-chunks1000-32k.json` |
| 2,000 | 8,000 | 36 | 2,327 | 1.4 | $0.56 | `e2e-sample-a-p10-keep-chunks2000-8k.json` |
| 2,000 | 16,000 | 46 | 4,171 | 6.2 | $0.78 | `e2e-sample-a-p10-reuse-chunks2000-16k.json` |
| 2,000 | 32,000 | 48 | 7,716 | 15.7 | $1.26 | `e2e-sample-a-p10-reuse-chunks2000-32k.json` |
| 4,000 | 8,000 | 31 | 1,878 | 0.2 | $0.49 | `e2e-sample-a-p10-keep-chunks4000-8k.json` |
| 4,000 | 16,000 | 44 | 3,779 | 2.6 | $0.73 | `e2e-sample-a-p10-reuse-chunks4000-16k.json` |
| 4,000 | 32,000 | 46 | 7,509 | 7.4 | $1.21 | `e2e-sample-a-p10-reuse-chunks4000-32k.json` |

The runs of phase 8 with whole sessions as episodes, for comparison:

| Run | Correct of 54 | Reader tokens | File |
|---|---|---|---|
| `search`, 16,000 | 34 | 1,738 | `e2e-sample-a-p8-search-16k.json` |
| `search`, 32,000 | 43 | 5,715 | `e2e-sample-a-p8-search-32k.json` |
| `search`, 64,000 | 45 | 12,849 | `e2e-sample-a-p8-search-64k.json` |
| `search`, 64,000, vector ranking, phase 10 | 46 | 13,405 | `e2e-sample-a-p10-keep-vector-search-64k.json` |

Chunks of 1,000 characters at 16,000 scored 48 with 4,336 reader tokens. The best whole-session
run scored 45 with 12,849 tokens. At 8,000 characters, the facts take most of the budget,
because they fill it before the episodes. At 32,000 characters with chunks of 1,000, the
block held 29.9 chunks on average, so `k`, 30, and not the budget, limited the episodes.

The block of `recall` shows a run of chunk sources as one range, as
[docs/contracts.md](contracts.md) gives. A fact line is still a little longer than with one
episode, so the list of known facts that the extractor reads can hold one fact less. The
keep runs of chunks of 1,000 and 4,000 made 108 model calls each, the 54 reader calls and
the 54 judge calls, so every extractor reply came from the cache. The keep run of chunks of
2,000 made 168 calls, so 60 extractor prompts missed the cache.
The smoke run with chunks of 2,000 at 64,000 scored 16 of 18, as in phase 9, for $0.71,
`e2e-smoke-p10-smoke-chunks2000.json`.

### The best chunk configuration on both samples

Phase 10 ran chunks of 1,000 characters at 16,000 on sample B, with `--keep`. It also
kept the memory of sample A again at 16,000 for the diagnosis. That run called no model,
and its 54 blocks had the same SHA-256 and verdict as the `--reuse` run of the table above.
Observed on 2026-10-18, with `src/hotmemory` at `12339f8`.

| Sample | Run | Correct of 54 | Reader tokens | Cost | File |
|---|---|---|---|---|---|
| A | chunks of 1,000, 16,000 | 48 | 4,336 | $0 | `e2e-sample-a-p10-keep-chunks1000-16k.json` |
| A | phase 8, `search`, 64,000 | 45 | 12,849 | | `e2e-sample-a-p8-search-64k.json` |
| B | chunks of 1,000, 16,000 | 46 | 4,360 | $2.05 | `e2e-sample-b-p10-keep-chunks1000-16k.json` |
| B | phase 8, `search`, 64,000 | 45 | 12,676 | | `e2e-sample-b-p8-search-64k.json` |

The diagnosis read the kept memory of each run. The phase 8 rows are those of the
diagnoses of `search` at 64,000.

| Run | Missed | Not extracted | Extracted, ranked below k | Recalled, all present | Recalled, some present | Evidence chunk or episode in block |
|---|---|---|---|---|---|---|
| A, chunks of 1,000, 16,000 | 6 | 2 | 0 | 2 | 2 | 6 |
| A, phase 8, `search`, 64,000 | 9 | 5 | 0 | 2 | 2 | 8 |
| B, chunks of 1,000, 16,000 | 8 | 2 | 1 | 2 | 3 | 7 |
| B, phase 8, `search`, 64,000 | 9 | 5 | 0 | 1 | 3 | 8 |

Files: the result files above with `-diagnosis` before `.json`. With chunks, a chunk of an
evidence session counts as that session in the last column. The diagnoses cost $0.08 on
sample A and $0.10 on sample B.

The retrieval score with the vector ranking, rerun on the 419 questions at the same commit,
gave the same sessions in the same order as the vector ranking of the ablation, on 419 of
419 questions: `recall_all@10` 0.9928. File: `retrieval-hotdata-local-isolated-vector.json`.

## Known limits

- The end-to-end numbers come from samples of 54 questions, so one question moves an arm
  by 1.9 points. The memory arm differs by 14.8 points between sample A and sample B. A
  run on all 500 questions waits for the owner's agreement.
- The full-context arm gives the reader every session. The reference code keeps the last
  50 sessions, so the two differ for histories longer than 50 sessions.
- The memory arm tried one extractor model, `gpt-4o-mini-2024-07-18`, with two prompts. A
  stronger extractor model was not measured.
- The diagnosis marks the facts of a question in one model call, and it can miss facts in
  a long list. Its count of misses with no stored fact can be too high.
- The reader prompt of the memory arm calls the block "facts extracted from history chats",
  also when the block holds episodes. A prompt that names the episodes was not tried.
- In the runs of phase 8, the local engine refused one fused search once, because it
  found a BM25 index of `memory_v1` stale against its table. A second try of the same run
  did not show the error. The cause was not found.
- `recall` keeps the records that are valid at `as_of`, and a search over history does not
  exist yet. A question about a value before it changed can need a superseded record.
