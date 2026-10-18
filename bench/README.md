# The benchmark runner

This folder runs hotmemory on LongMemEval-S, a benchmark of 500 questions over long chat
histories. It is not part of the hotmemory package. [docs/benchmarks.md](../docs/benchmarks.md)
reports the numbers, and each number names the result file that it comes from.

## Before a run

1. Copy `.env.template` to `.env` at the root of the repository, and set `OPENAI_API_KEY`.
2. Start the local stack with `make local-up`.
3. Run the commands from the root of the repository. The first run downloads the data
   file to `bench/data/` and checks its SHA-256. Run this command to download it:

```sh
uv run --group bench python -c "from bench.data import download; print(download())"
```

The embedder keeps each vector in `bench/cache/`, so a second run calls no model. Git
ignores `bench/data/` and `bench/cache/`.

## The retrieval score

This command writes one episode for each session of each scored question, searches with
the question, and scores the ranked sessions:

```sh
uv run --group bench --env-file .env python -m bench.retrieval
```

The options are these:

| Option | Values | Default |
|---|---|---|
| `--driver` | `hotdata` or `memory` | `hotdata` |
| `--mode` | `isolated` (one question at a time in the tables) or `shared` (every question in the tables) | `isolated` |
| `--ranking` | `fused`, `vector`, or `both` | `both` |
| `--location` | `local` or `cloud` | `local` |
| `--url` | the address of the local engine | `http://localhost:3000` |
| `--limit` | the first N scored questions | every scored question |
| `--ablate` | also rank with the fused query less one arm at a time: `bm25-content`, `content-cue`, and `bm25`; needs `--driver hotdata` | off |

With `--location cloud`, the runner reads the connection from `HOTDATA_API_KEY`,
`HOTDATA_WORKSPACE`, and `HOTDATA_API_URL`, so set them in `.env` first. It ignores `--url`.

Each run writes one JSON file into `bench/results/`. The file holds the configuration, the
mean metrics over every question and over each question type, the timings, and the ranked
sessions of each question. Git ignores `bench/results/`, and
[docs/benchmarks.md](../docs/benchmarks.md) records the reported numbers.

## The end-to-end score

This command runs three arms on a sample of 54 questions, 9 of each question type:

```sh
uv run --group bench --env-file .env python -m bench.e2e
```

Without `--run` or `--replay`, it prints the cost estimate of each arm and calls no model.
With `--run`, it stops if the estimate is over 50 dollars, and else it calls the models.
Set `OPENAI_MODEL_KEY` in `.env` first. With `--replay`, it takes every reply from the
cache, and it stops at the first reply that the cache does not hold. The estimate does not
subtract the cached replies, so the stop at 50 dollars applies to a replay too.

| Arm | What the reader gets |
|---|---|
| `full` | Every session of the question. |
| `retrieval` | The 10 sessions that the search ranks first. |
| `memory` | The block of `recall`, after `capture` ran on each session in date order. |

The reader and the judge are `gpt-4o-2024-08-06` at temperature 0, with the prompts of
the reference code. The baseline extractor of the memory arm is `gpt-4o-mini-2024-07-18`.
Each reply is kept in `bench/cache/`, so a second run with the same prompts calls no model.

There are two samples. Sample A is `bench/sample.json`, drawn with the seed `20261010`.
Each phase iterates on sample A. Sample B is `bench/sample-b.json`, drawn with the seed
`20261013` from the questions that are not in sample A. A phase runs sample B once, at its
end.

The options are these:

| Option | Values | Default |
|---|---|---|
| `--sample` | `a` or `b` | `a` |
| `--arms` | a comma-separated list of `full`, `retrieval`, and `memory` | every arm |
| `--retrieval-ranking` | `vector` or `fused`, the ranking of the retrieval arm | `vector` |
| `--memory-ranking` | `vector` or `fused`, the ranking of `capture` and `recall` | `vector` |
| `--recall-k` | the `k` of the memory recall | `30` |
| `--recall-budget` | the `budget` of the memory recall, in characters | `8000` |
| `--extractor` | a variant name in `VARIANTS` of `bench/prompts.py` | `baseline` |
| `--no-fact-sources` | write the facts with no session source, as phases 5 and 6 did | off |
| `--session-episodes` | capture each session as an episode, with the facts citing it | off |
| `--chunk-chars` | store each session as chunks of at most this many characters, with each fact citing every chunk; needs `--session-episodes` | off |
| `--episodes` | `none`, `cited`, or `search`, the `episodes` of the memory recall; a value other than `none` needs `--session-episodes` | `none` |
| `--replay` | call no model, and stop at the first reply that the cache does not hold | off |
| `--workers` | the number of processes that run the questions | `1` |
| `--keep` | keep the memory of each question in a local database of its own; needs `--arms memory` | off |
| `--reuse` | recall from the kept memory, and skip `capture`; needs `--arms memory` | off |
| `--smoke` | run the 18 questions of the smoke sample on `MemoryStore`, one store in each worker | off |
| `--resume` | run only the questions that the rows file of an earlier run does not hold | off |
| `--tag` | a suffix for the name of the result file | none |
| `--limit` | the first N questions of the sample | every question |

Each worker gets the questions in turn. Without `--keep` or `--reuse`, it runs them over a
new local database of its own, and empties the namespaces of each question after it. Each
table then holds one question at a time, as in a run with one worker. With
`--memory-ranking fused`, BM25 ranks over the whole table before the scope filter, so the
rows of a second question in a table can change the ranking. An extractor variant names a prompt and a model. Each fact carries
the source `session:<hash>`, where the hash is the first 12 hex digits of the SHA-256 of
the session id. The block shows the sources to the reader, and the id of an evidence
session holds `answer`, so the source does not show the id. If an extractor reply reaches
its limit of 4,000 output tokens, the session writes no facts. The cache keeps that reply,
so a replay skips the same session. The result file counts these sessions in
`sessions_at_limit`.

With `--session-episodes`, `capture` stores each session as an episode under the key
`session-<hash>`, with the same hash. Each fact then cites that episode, and carries no
`session:<hash>` source. The block shows the source as `session-<hash>@1`. With `--chunk-chars` as well, `capture`
stores the session as chunks under `session-<hash>-<n>`, each fact cites every chunk, and
the block shows the sources as one range, `session-<hash>-0001…<last>@1`. The source
holds no run id, so a second run with another `--episodes` or `--recall-budget` replays
`capture` from the cache. The result file lists the episode keys of each block in
`episodes_in_block`, and the SHA-256 of each block in `block_sha256`. The result file is
`bench/results/e2e-sample-<a or b>[-<tag>].json`, or `bench/results/e2e-smoke[-<tag>].json`
for a smoke run, and it records each option.

## Faster runs

The episodes value, the budget, and `k` act in `recall`, after `capture`. So a run that
changes only these values, or the reader, can use the memory of an earlier run. The
memory arm captures the memory once with `--keep`, and a later run with `--reuse` skips
`capture`:

```sh
uv run --group bench --env-file .env python -m bench.e2e --run --arms memory --extractor detail --session-episodes --episodes search --recall-budget 64000 --workers 4 --keep
uv run --group bench --env-file .env python -m bench.e2e --run --arms memory --extractor detail --session-episodes --episodes cited --recall-budget 32000 --workers 4 --reuse
```

With `--keep`, each question runs over a new local database of its own, and the database
stays after the run. One database holds one question. With `--memory-ranking fused`, BM25
ranks the whole table before the namespace filter, so the memory of a second question
would change the block. The
name of the database is `hotmemory-kept-<sample>-<extractor><digest>-<cites>-<ranking>-<commit>-<question id>`:

| Part | What it holds |
|---|---|
| `<extractor><digest>` | The variant name, and 6 hex digits of the SHA-256 of its model and prompt. |
| `<cites>` | `episodes` with `--session-episodes`, `chunks<size>` with `--chunk-chars` as well, `sources` with fact sources, or `plain`. |
| `<ranking>` | The `--memory-ranking`. |
| `<commit>` | The last commit that changed `src/hotmemory`. |

The runner refuses `--keep` if `src/hotmemory` holds a change that is not committed, or if
a kept database of the run exists. If a question fails, its database is deleted. With
`--reuse`, the runner computes the same names, and refuses to run if a database does not
exist. It never deletes a kept database. A reused row has `null` for `facts_written` and
`sessions_at_limit`, because `capture` did not run. The result file records the name in
`memory.kept`. The cost estimate of a run with `--reuse` still counts the extraction, so it
is too high. If a commit changes `src/hotmemory`, no later run can use the kept databases.
Delete them by hand.

A smoke run checks that a change runs and that the block looks right:

```sh
uv run --group bench --env-file .env python -m bench.e2e --run --smoke --arms memory --extractor detail --session-episodes --episodes search --recall-budget 64000
```

It runs the 18 questions of `bench/sample-smoke.json`, 3 of each type drawn from sample A
with the seed `20261016`. Each worker runs its questions over a `MemoryStore` of its own,
in its own process, and empties the memory of each question after it. It decides no gain: 18
questions can be off by about 23 points. `MemoryStore` ranks by vector only, as
`HotdataStore` does by default. With `--memory-ranking fused`, the ranking differs, so
`capture` shows the extractor another list of known facts than a run on the local engine.
The first smoke run of a configuration pays for the extraction that the cache does not
hold, and a later one replays it from the cache. Pass `--workers 4` to make the first run
faster. `--smoke` refuses `--keep`, `--reuse`, and `--sample`.

Each run writes the file `bench/results/<name>.rows.jsonl` as it goes, where `<name>` is
the name of its result file. The first line holds the arms, the configuration, the sample,
the limit, and the kept name. Each later line holds the row of one finished question. If a
call to a model or to the engine fails, the question fails, and the next question runs. If
the runner then cannot empty the namespaces of that question, the worker stops. A
`CacheMiss` of `--replay` still stops the run. If a question failed, or the run stopped,
the runner writes no result file. Run the same command again with `--resume`: it skips the
questions that the rows file holds. It refuses a rows file of another configuration, and a
run without `--resume` refuses to start over a rows file. When every question has
finished, the runner writes the result file and deletes the rows file.

## The checks of the results

This command compares the vector rankings of two retrieval result files, as the driver
check does:

```sh
uv run --group bench python -m bench.compare bench/results/<first>.json bench/results/<second>.json
```

This command finds the cause of each miss of the memory arm in a result file. The default
file is `e2e-sample-a.json`, and `--result` names another one:

```sh
uv run --group bench --env-file .env python -m bench.diagnose --result bench/results/e2e-sample-b.json
```

The command runs `capture` again with the ranking, the `k`, the `budget`, and the extractor
variant that the result file records. The replay calls no model, and it stops at the first
reply that the cache does not hold. If the result file names kept databases, from a run
with `--keep` or `--reuse`, the command reads the memory of each question from its kept
database and does not run `capture`. GPT-4o marks the stored facts that hold each answer.
Each miss then gets one of four causes:

| Cause | Meaning |
|---|---|
| not extracted | No stored fact holds the answer. |
| extracted, ranked below k | A fact holds the answer, but the search ranks it below `k`. |
| in the top k, cut by as_of or the budget | A fact is in the top `k`, but `recall` leaves it out. |
| recalled, reader missed | A fact that holds the answer is in the block. |

For the last cause, GPT-4o tells whether the marked facts in the block hold every value
that the answer needs ("all present") or only some of them ("some present"). The command
prints the groups for each question type, and writes them next to the result file, as
`<result>-diagnosis.json`. For each fact that holds the answer, the file gives its session
id, if the fact has a source. If the run recalled episodes, each miss also gets
`evidence_in_block`: whether the block held an evidence session as an episode. The table
then counts these misses in a last column.