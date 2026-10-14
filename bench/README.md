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
| `--retrieval-ranking` | `fused` or `vector`, the ranking of the retrieval arm | `fused` |
| `--memory-ranking` | `fused` or `vector`, the ranking of `capture` and `recall` | `fused` |
| `--recall-k` | the `k` of the memory recall | `30` |
| `--recall-budget` | the `budget` of the memory recall, in characters | `8000` |
| `--extractor` | a variant name in `VARIANTS` of `bench/prompts.py` | `baseline` |
| `--no-fact-sources` | write the facts with no session source, as phases 5 and 6 did | off |
| `--replay` | call no model, and stop at the first reply that the cache does not hold | off |
| `--workers` | the number of processes that run the questions | `1` |
| `--tag` | a suffix for the name of the result file | none |
| `--limit` | the first N questions of the sample | every question |

Each worker gets the questions in turn, and runs them over a new local database of its
own. Each table then holds one question at a time, as in a run with one worker. BM25 ranks
over the whole table before the scope filter, so the rows of a second question in a table
can change the ranking. An extractor variant names a prompt and a model. Each fact carries
the source `session:<hash>`, where the hash is the first 12 hex digits of the SHA-256 of
the session id. The block shows the sources to the reader, and the id of an evidence
session holds `answer`, so the source does not show the id. If an extractor reply reaches
its limit of 4,000 output tokens, the session writes no facts. The cache keeps that reply,
so a replay skips the same session. The result file counts these sessions in
`sessions_at_limit`. The result file is `bench/results/e2e-sample-<a or b>[-<tag>].json`,
and it records each option.

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
reply that the cache does not hold. GPT-4o marks the stored facts that hold each answer.
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
id, if the fact has a source.