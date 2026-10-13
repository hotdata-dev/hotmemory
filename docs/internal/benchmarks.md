# Benchmarks: which ones to run, and what they ask of the contracts

Date: 2026-10-08. Status: research, no code. This file picks the public benchmarks that
hotmemory runs. It says how each one connects to the two contracts. It lists the contract
questions that the benchmarks expose. Phase 5 in `roadmap.md` builds the runner. This file
exists now so that phases 2 and 3 build a contract that a benchmark can drive.

This file is a record of the research and is not updated. Phase 5 ran the benchmark on
2026-10-10, and `docs/benchmarks.md` holds the observed numbers. Where the two disagree,
`docs/benchmarks.md` is right. For example, the loader counted 43 late sessions that are
evidence, where section 2.1 says 75.

Four benchmarks were read: DolphinBench, LongMemEval, LoCoMo, and supermemory's MemoryBench.
Each one was read from its code, its data, and its paper, in a shallow clone. The
per-benchmark notes with file and line references are not committed here.

Evidence labels. [counted] means that the number was counted from the released data file
on 2026-10-08. [read] means that the number comes from a paper, a blog, or a README, and
nobody here reproduced it. [estimate] means arithmetic on stated assumptions. Nothing in
this file was run against hotmemory, so no number here is [measured].

## 1. The answer

Run LongMemEval-S first, with our own Python runner. Run LoCoMo second, as a fast and
cheap smoke test. Use MemoryBench later, only to compare hotmemory with other memory
layers under one reader and one judge. Do not run DolphinBench before an agent exists
around hotmemory, because it grades a whole agent and not a memory layer.

| Benchmark | What it grades | Retrieval recall with gold labels | Ingest size | Fit now |
|---|---|---|---|---|
| LongMemEval-S | Answers to 500 questions over chat histories | Yes, session and turn ids | 23,867 sessions [counted] | First choice |
| LoCoMo | Answers to 1,540 questions over 10 long conversations | Yes, turn ids, with label errors | 272 sessions [counted] | Smoke test |
| MemoryBench | A TypeScript runner around LongMemEval, LoCoMo, and ConvoMem | No, a judge decides relevance | Same as the benchmark it wraps | Comparison later |
| DolphinBench | An agent that takes tool actions that depend on memory | No | 13,539 messages [counted] | After phase 4 |

The reasons follow in sections 2 to 5. Section 6 is the contract review, which is the
main purpose of this file. Section 7 gives the code layout, and section 8 the models and
costs.

## 2. LongMemEval-S

LongMemEval tests five memory abilities: extraction from one session, reasoning across
sessions, temporal reasoning, knowledge updates, and abstention (the reader says that the
history does not hold the answer). Code and data are MIT. The current data file is
`longmemeval_s_cleaned.json`, released after a cleanup in 2025-09.

### 2.1 The data

| Item | Value |
|---|---|
| Questions | 500 [counted] |
| Question types | multi-session 133, temporal-reasoning 133, knowledge-update 78, single-session-user 70, single-session-assistant 56, single-session-preference 30 [counted] |
| Abstention questions | 30, the ids that end in `_abs`, spread over four types [counted] |
| Sessions per question | 38 to 62, mean 47.7 [counted] |
| Turns per question | 396 to 616, mean 494 [counted] |
| Tokens per question | about 101,600 [counted] |
| All sessions | 23,867, of which 19,195 have a unique id [counted] |
| All turns | 246,750, of which 122,416 are user turns [counted] |
| Evidence labels | `answer_session_ids` per question, and `has_answer` on the turns inside them |
| Session timestamp | One per session, for example `2023/05/30 (Tue) 23:40`, no time zone |
| Question time | `question_date`, in the same format |

Each question has its own haystack (the set of sessions that the system ingests before it
answers). The reference runner builds a new index for each question. So one question maps
to one namespace.

Two facts in the data contradict the README, and a runner must handle both.

1. In 211 of the 500 haystacks, the sessions are not in date order [counted]. The runner
   sorts by date before it ingests, because supersession depends on order.
2. In 76 questions, a session is dated after `question_date`, always on the same day, by
   up to 23.7 hours [counted]. In 75 of them, that session is evidence. A
   `recall(as_of=question_date)` drops it. The runner sets `as_of` to the end of the day
   of `question_date`.

### 2.2 The two scores

Retrieval recall needs no model. The reference code ranks corpus ids for each question and
compares them with the gold ids. It reports `recall_any@k`, `recall_all@k`, and
`ndcg_any@k` for k in 1, 3, 5, 10, 30, and 50. Session-level retrieval indexes user turns
only. Turn-level ids are `{session_id}_{turn_index + 1}`, where the index counts every
turn. The scored set skips abstention questions. It also skips questions with no user-side
evidence turn. That leaves 419 questions [counted from the data, the script was not run]. The paper
reports retrieval only on the M split, so no official S baseline exists [read].

End-to-end accuracy needs two models. A reader model gets the retrieved sessions, sorted by
date, and the line `Current Date: {question_date}`, and answers with a chain-of-thought
prompt. A judge model compares the answer with the gold answer. The reference judge is
`gpt-4o-2024-08-06` at temperature 0, with one prompt per question type. The temporal
prompt accepts an answer that is one day off. The knowledge-update prompt accepts an answer
that gives the old value and the new value. The paper reports 0.97 to 0.98 agreement
between this judge and human experts [read].

### 2.3 Published numbers

All numbers are accuracy on S, from vendor pages [read]. The vendors do not say whether they
used the cleaned data. Make sure that a figure matches its page before you quote it.

| System | Accuracy | Reader | Note |
|---|---|---|---|
| Full context, paper | 60.6% | GPT-4o | Oracle sessions only: 87.0% |
| Zep | 71.2% | GPT-4o | Its full-context baseline is 60.2% |
| supermemory | 81.6%, 84.6%, 85.2% | GPT-4o, GPT-5, Gemini 3 Pro | A later page says 97% as "Recall@20 with aggregation", which is a different metric |
| Mastra | 84.23%, 94.87% | GPT-4o, GPT-5-mini | |
| Emergence | 79% to 86% | not stated | Three variants |
| Hindsight | 83.6% to 91.4% | | Judged by GPT-OSS-120B, not GPT-4o |
| mem0 | 94.4% | not stated | Split and judge not stated |

The only comparable column is GPT-4o as reader and GPT-4o as judge. Full context with
GPT-4o is about 60%. A memory layer that scores below that adds nothing.

### 2.4 Why it is first

- It scores retrieval with gold labels and no model. That isolates the store and the
  retrieval query, which is what phases 2 and 3 build.
- Its question types map to the record's fields. Knowledge-update tests `supersede`.
  Temporal-reasoning tests `observed_at`, `valid_from`, and `as_of`. Abstention tests
  whether `recall` returns too much.
- It is the benchmark that the field reports, and `roadmap.md` already names it.
- Its license is MIT.

## 3. LoCoMo

LoCoMo is ten long conversations between two speakers, with questions about them. The
data is CC BY-NC 4.0, which forbids commercial use. An internal evaluation is likely
inside that license. Nobody here asked a lawyer.

| Item | Value |
|---|---|
| Conversations | 10, two speakers each [counted] |
| Sessions | 272, 19 to 32 per conversation [counted] |
| Turns | 5,882 [counted] |
| Tokens | 184,631 text-only, about 18,500 per conversation [counted] |
| Scored questions | 1,540: single-hop 841, temporal 321, multi-hop 282, open-domain 96 [counted] |
| Excluded | 446 adversarial questions. 444 of them have no answer field [counted] |
| Evidence | Turn ids such as `D3:7` per question. 9 are malformed [counted] |
| Session timestamp | One per session, for example `1:56 pm on 8 May, 2023` |

The labels are weak. An independent audit found 99 wrong gold answers in 1,540, which caps
the best score at 93.57% [read]. The same audit found that a judge accepted 62.81% of
answers that were wrong but on topic [read]. The mem0 judge prompt accepts an answer that
"touches on the same topic". Thus a LoCoMo score says little about accuracy.

The published numbers disagree with each other. The mem0 paper gives mem0 66.88 J (the
score from the judge model) and full context 72.90 [read]. Zep reran its own system with a
corrected setup and reported 75.14 [read]. Letta reported 74.0% for an agent with plain
file tools [read]. mem0 later claimed 92.5 with GPT-5 and a more lenient judge, but its
committed result file says 91.56% [read]. Full context beats every memory layer in the
2025 numbers.

LoCoMo is useful for two things. First, it is small: a full run is ten namespaces and 272
sessions, so it finishes in minutes on the local stack. Second, the mem0 paper reports
search latency at p50 and p95 for five systems, and that is the only public latency table
[read]. mem0 reports 0.148 s p50 and 0.200 s p95 for search. Zep reports 0.513 s and
0.778 s. The original LoCoMo code also computes retrieval recall against the evidence turn
ids, which the mem0 runner does not.

## 4. MemoryBench

MemoryBench is a runner, not a dataset. It is TypeScript on Bun, under MIT, and it wraps
LongMemEval-S, LoCoMo, and ConvoMem. It ships providers for supermemory, mem0, Zep, a
filesystem, and plain RAG. Its value is comparison: one command runs several memory layers
with the same reader and the same judge.

The provider interface is small (`src/types/provider.ts`):

```ts
initialize(config)
ingest(sessions: UnifiedSession[], { containerTag }) -> { documentIds, taskIds? }
awaitIndexing(result, containerTag, onProgress?)
search(query, { containerTag, limit?, threshold? }) -> unknown[]
clear(containerTag)
```

A session reaches `ingest` with its messages and `metadata.date` as ISO. The runner calls
`ingest` once per session, in order, and `awaitIndexing` once per question. `search` always
gets the raw question, `limit: 10`, and `threshold: 0.3`. It does not get the question date.
The container tag is `{questionId}-{runId}`, one per question.

Use it later and not first, for these reasons.

- The retrieval metrics are not the LongMemEval metrics. A judge model decides whether each
  result is relevant, and no gold label is used. The code sets `recall@k` to 1 for any
  relevant result. Thus it equals hit@k (issue #67).
- A Python library joins through an HTTP shim. There is no plugin mechanism. A new provider
  edits three files in the TypeScript tree.
- The repository has no tests and no CI. A batch of correctness issues from 2026-08-13 is
  still open, with fixes unmerged (issues #60 to #78).
- The search call carries no question date, so `recall` cannot use `as_of`. Temporal
  questions run without it.

It reports what we want to report: accuracy per question type, latency per phase at p50,
p95, and p99, and context tokens per question. Serial ingest is one flag:
`--concurrency-ingest 1`.

## 5. DolphinBench

DolphinBench comes from mem0, released 2026-09-21, under Apache-2.0. It does not ask
questions. It gives an agent a task in simulated apps, for example "send the email", and
checks the arguments of the tool call. The right arguments depend on facts from the history.

| Item | Value |
|---|---|
| Personas | 3, with 5,011, 3,400, and 5,128 sessions [counted] |
| Session shape | One user message with a dated narrative time. No assistant turns |
| History | 2023-01 to 2027-12, about 500,000 user tokens per persona [read] |
| Tests | 600, 200 per persona. 264 depend on one fact, the rest on 2 to 9 [counted] |
| Checks | 1,194 exact field checks and 610 judge checks. Every check must pass for the test to pass |
| Judge | Fixed to an Azure OpenAI model. The runner refuses a paid run with another judge |

The adapter does not see a memory interface. It sees `run_interaction`, which sends one
message to the agent, and `freeze`, after which memory is read-only. hotmemory needs an
agent loop, tools that call `recall` and `capture`, and Azure OpenAI credentials for the
judge. The published runs cost $96 for the cheapest configuration and $1,133 to $1,831 for
Claude Code with Sonnet 5, without the judge [read]. The repository had 10 commits at the
time of reading, and its own driver document says that a paid end-to-end run is
unverified.

DolphinBench measures what the first consumer does: an agent that must act on a fact from
memory. When phase 4 gives an agent around hotmemory, run it. The MCP server in phase 6 is the
other entry point. It does not test the contract in isolation.

## 6. What the benchmarks ask of the contracts

This section is the main result. Each item states what a benchmark needs, whether the
contract gives it today, and which phase decides the open ones.

### 6.1 What the contract already gives

1. Gold ids fit in `sources`. LongMemEval scores session ids and turn ids. LoCoMo scores
   turn ids such as `D3:7`. A record from `capture` carries
   `sources=("session:<id>", "turn:<id>_<n>")`, and the runner maps a ranked list of
   records to a ranked list of unique session ids. No new field is needed.
2. The session date fits in `observed_at`. Every benchmark gives one timestamp per session
   and none per turn. `capture(text, scope, actor, observed_at, extractor)` takes it. The
   record refuses a timestamp without a time zone, so the runner attaches UTC.
3. One question or one conversation fits in one namespace. For example, the namespace for
   question `q123` in run `r7` is `("bench", "lme_s", "r7", "q123")`.
   `recall(scopes=[that namespace])` matches the isolation that every runner expects.
4. Knowledge updates fit `supersede`, and temporal questions fit the two clocks.
5. A raw-turn baseline needs no model. Turns go into the `episode` table, and retrieval
   runs over them. This is the same shape as the reference retrieval runner. It measures
   the store before extraction exists.

### 6.2 Questions for phase 2, the Hotdata driver

1. The cost of a write decides the ingest plan. A cloud load costs about 2.1 s, and a local
   load costs about 24 ms (measured in phase 0). The estimates below assume one table and
   one writer.

   | Write pattern | LongMemEval-S, cloud | LongMemEval-S, local | LoCoMo, cloud |
   |---|---|---|---|
   | One load per question or conversation | 500 loads, about 18 min | under 1 min | 10 loads, about 21 s |
   | One load per session | 23,867 loads, about 14 h | about 10 min | 272 loads, about 10 min |
   | One load per turn | about 247,000 loads, about 144 h | about 1.6 h | 5,882 loads, about 3.4 h |

   All values are [estimate]. Proposal: run the full ingest on the local stack, and run the
   latency numbers on the cloud for a subset of questions. Report the two separately.
2. Many namespaces share one table lock. The 500 namespaces of one run are in one table, so
   their loads serialize. Runners also run questions in parallel: the mem0 runner used ten
   workers, and MemoryBench takes a concurrency flag. Phase 2 states whether one `writer`
   is safe across threads in one process. Extraction can then run in parallel while one
   writer sends the loads.
3. The search must reach the `episode` table. The raw-turn baseline in 6.1 searches
   episodes, and a fact links back to its episode through `sources`. Today `Store.search`
   names no table. Phase 2 states how a caller searches episodes. A filter on
   `kind="episode"` is one way, and it needs no new surface.
4. The table grows to benchmark size. The `episode` table for one LongMemEval-S run holds
   122,416 user turns, or 246,750 with assistant turns [counted]. In phase 0, a plain vector
   index on 100,000 rows took 65.8 s to build in the cloud and 21.9 s locally [measured].
   A load of 100,000 rows with embeddings took 62.9 s in the cloud [measured]. Phase 2
   decides when the driver builds indexes: before the load, or once after a bulk load.
5. Latency measures the store, not the network. A cold engine costs 8 to 10 s on the
   first call. The runner warms the engine and reports cold start as a separate number.
   The cloud floor of a query request is about 400 ms. Thus a cloud search will not reach
   mem0's reported 148 ms p50. A local run shows the engine cost alone. Report both, with
   the setup named.

### 6.3 Questions for phase 3, the memory contract

1. A buffered write is invisible to `capture`. `capture` calls `recall` before it extracts,
   so that the extractor can skip a known fact and supersede an old one. If the writer
   flushes once per question, every session in the question is extracted against an empty
   memory. Then supersession across sessions disappears, and knowledge-update questions
   test nothing. There are three options.
   - Flush per session. This is slow in the cloud and fast locally (6.2, item 1).
   - Let `recall` inside one `Memory` see its own unflushed buffer. This changes the rule
     "a write is visible to the next `recall`", and it is a change to `guarantees.md`.
   - Extract offline in date order with an in-process view, and load once. The benchmark
     then no longer tests `capture` as a consumer calls it.

   The benchmark is the first consumer that ingests history in bulk, so phase 3 decides
   this before the runner exists. The first option on the local stack is the simplest
   proposal.
2. `recall` hides the past states that some questions need. `recall` keeps the records that
   are valid at `as_of`. That is right for knowledge-update questions. Some
   temporal-reasoning questions ask about a value before it changed, for example "what was
   my commute before I moved". Phase 3 decides whether `recall` takes an option to include
   superseded records with their validity spans. The rendered block already prints the
   span, so the reader can tell old from new.
3. The `as_of` time needs a stated rule for dates without a time. LongMemEval evidence can
   be dated up to 24 hours after `question_date`, on the same day. The runner passes the end
   of the day. The contract states whether `as_of` is inclusive and what a caller passes
   when the source has a date and no time.
4. The budget is in characters, and the benchmarks count tokens. MemoryBench reports
   context tokens per question, and the mem0 paper compares memory layers by token count.
   The runner counts the tokens of the rendered block with one named tokenizer, and the
   contract keeps characters.
5. A distance is not a score. MemoryBench passes `threshold: 0.3`, which assumes a score
   where higher is better. The shim ignores it, or converts the distance as
   `guarantees.md` already says that an adapter does.
6. The extractor needs the session as a unit. Benchmarks hand over a session with two
   roles. Most LongMemEval tokens are assistant text: 44.4 million of 50.8 million
   [counted]. The extractor needs the session, its date, and the role of each turn.
   `capture` takes text, so the runner renders a session as text with role labels. Phase
   3 makes sure that this is enough, or adds a structured input.

### 6.4 Order of the phases

The order in `roadmap.md` is right, with one addition. A small retrieval slice can join
phase 2 at low cost. Take 20 questions of LongMemEval-S, write their user turns as episodes
on the local stack, and score `recall_any@10` against the gold session ids. The slice needs
no model except the embedder. It proves that the driver, the `sources` convention, and the
namespace per question work together, three phases before the full runner. The
`MemoryStore` oracle test can run the same slice and compare the two drivers.

Phase 5 then adds extraction, the reader, and the judge on top of a path that already
works.

## 7. The code setup

The runner lives outside the library, in a `bench/` folder at the root of the repository.
It is not part of the package, and the offline test suite does not import it. Datasets
download to a folder that git ignores.

```text
bench/
  README.md               how to run, which models, the cost of a run
  common/
    ids.py                the sources convention: "session:<id>", "turn:<id>_<n>"
    clock.py              parsers for the session dates of each benchmark, to UTC
    embedder.py           the embedder callable that both drivers take
    extractor.py          the extractor callable for capture, one model call per session
    reader.py             the reader prompt and call
    judge.py              the judge prompts per question type, copied from the reference code
    metrics.py            recall_any@k, recall_all@k, ndcg_any@k, accuracy per type, latency p50/p95/p99
    report.py             writes one JSON result and one Markdown table per run
  longmemeval/
    data.py               download, sort sessions by date, the end-of-day as_of rule
    retrieval.py          the no-model score: episodes in, ranked session ids out
    e2e.py                capture, recall, reader, judge
  locomo/
    data.py               parse the session dates, repair the 9 bad evidence ids
    e2e.py
  memorybench_shim/
    server.py             FastAPI over Memory: /ingest, /flush, /search, /clear
    provider.ts           a MemoryBench provider that calls the shim
```

The dependencies go in an optional group in `pyproject.toml`, so that the library keeps no
runtime dependency:

```toml
[dependency-groups]
bench = ["anthropic", "openai", "huggingface-hub", "tiktoken", "fastapi", "uvicorn"]
```

The core loop of `longmemeval/e2e.py`, in outline:

```python
for q in questions:  # one namespace per question
    scope = ("bench", "lme_s", run_id, q.id)
    for s in sorted(q.sessions, key=lambda s: s.date):
        memory.capture(
            text=render_session(s),  # role-labeled turns
            scope=scope,
            actor="bench-extractor",
            observed_at=s.date,  # UTC
            extractor=extract,  # writes sources=("session:<id>", ...)
        )
    t0 = time.perf_counter()
    hits = memory.recall(q.question, scopes=[scope], as_of=end_of_day(q.date), budget=8000)
    search_ms = (time.perf_counter() - t0) * 1000
    answer = read(q.question, hits.block, current_date=q.date)
    verdict = judge(q.type, q.question, q.answer, answer)
```

The shim for MemoryBench maps its five calls onto `Memory`. `ingest` calls `capture` for
each session inside a `writer`. `awaitIndexing` flushes the writer. `search` calls `recall`
with no `as_of`. `clear` calls `forget` on the namespace.

The commands, as phase 5 ships them:

```sh
make local-up
uv run --group bench --env-file .env bench/longmemeval/retrieval.py --limit 20
uv run --group bench --env-file .env bench/longmemeval/e2e.py --sample-per-type 9
uv run --group bench --env-file .env bench/locomo/e2e.py
```

A run writes its configuration (models, embedder, k, budget, driver, local or cloud, data
file hash) next to its numbers. A number without its configuration is not reported.

## 8. Models and cost

A benchmark run needs four models. hotmemory ships none of them, by the brief. The runner
supplies them.

| Role | Used in | Proposal | Reason |
|---|---|---|---|
| Embedder | Every search | An OpenAI embedding model, or a local sentence-transformer model | Anthropic offers no embedding model. A local model keeps the retrieval score free and repeatable. |
| Extractor | `capture` | Claude Haiku 4.5, with Claude Sonnet 5.5 as a second point | One call per session. This is the largest cost. |
| Reader | End-to-end accuracy | GPT-4o for the published number, and a Claude model for internal runs | Every vendor number on LongMemEval-S uses GPT-4o. |
| Judge | End-to-end accuracy | `gpt-4o-2024-08-06`, as in the reference code | A different judge makes the number incomparable. |

The keys are `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` in `.env`, beside the Hotdata keys
that the measurement scripts already read.

The estimates below assume 1,000 tokens of prompt overhead per extraction call, an 8,000
token reader context, and list prices on 2026-10-08. All values are [estimate]. Make sure
that the prices are current before a paid run.

| Run | Model calls | Cost |
|---|---|---|
| LongMemEval-S, retrieval on raw turns | embeddings only | under a few dollars, or none with a local embedder |
| LongMemEval-S, extraction per session | 23,867 | about $93 with Haiku 4.5, about $185 with Sonnet 5.5. The batch API halves this |
| LongMemEval-S, reader | 500 | about $5 to $13 |
| LongMemEval-S, judge | 500 | under $1 |
| LongMemEval-S, a sample of 9 per type | 54 questions | about one tenth of the above |
| LoCoMo, a full run | 272 extractions, 1,540 reads and judgments | about $15 to $35 |

Start every new configuration with the sample of 54 questions. A full run follows only when
the sample works.

## 9. What was not confirmed

- No benchmark script was run. Every count above comes from the data files, and the 419
  scored retrieval questions come from the data, not from the reference script.
- The vendor numbers came through web pages and were not reproduced. No vendor states
  whether it used the cleaned LongMemEval data.
- The DolphinBench leaderboard loads by script, and the live entries were not seen. The
  paper PDF was not read.
- The LongMemEval M split was not downloaded.
- Whether Hotdata accepts reads in parallel during a load was not measured.
- The license position of LoCoMo for internal use was not reviewed by a lawyer.
