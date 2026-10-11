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
| `--limit` | the first N scored questions | every scored question |

Each run writes one JSON file into `bench/results/`. The file holds the configuration, the
mean metrics over every question and over each question type, the timings, and the ranked
sessions of each question.

## The end-to-end score

This command runs three arms on the sample of 54 questions in `bench/sample.json`, 9 of
each question type:

```sh
uv run --group bench --env-file .env python -m bench.e2e
```

Without `--run`, it prints the cost estimate of each arm and calls no model. With `--run`,
it stops if the estimate is over 50 dollars, and else it calls the models. Set
`OPENAI_MODEL_KEY` in `.env` first.

| Arm | What the reader gets |
|---|---|
| `full` | Every session of the question. |
| `retrieval` | The 10 sessions that the fused search ranks first. |
| `memory` | The block of `recall`, after `capture` ran on each session in date order. |

The reader and the judge are `gpt-4o-2024-08-06` at temperature 0, with the prompts of
the reference code. The extractor of the memory arm is `gpt-4o-mini-2024-07-18`. Each
reply is kept in `bench/cache/`, so a second run with the same prompts calls no model.
`--arms` runs some of the arms, and `--limit` runs the first N questions of the sample.

## The checks of the results

This command compares the vector rankings of two retrieval result files, as the driver
check does:

```sh
uv run --group bench python -m bench.compare bench/results/<first>.json bench/results/<second>.json
```

This command finds the cause of each miss of the memory arm in `e2e-sample.json`. It runs
`capture` again from the cached replies, so the extractor is not called again. GPT-4o marks
the stored facts that hold each answer:

```sh
uv run --group bench --env-file .env python -m bench.diagnose
```
