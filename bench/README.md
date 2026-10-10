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
