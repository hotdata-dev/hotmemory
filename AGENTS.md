# Agent instructions

This file is for coding agents that work in this repository. It gives the commands and the
rules, and points to the files that hold everything else.

## Commands

```sh
make verify       # the full check; run it before each commit
make local-up     # start a local RuntimeDB container
make local-down   # stop it
cp .env.template .env            # then fill in .env; it is ignored by git
uv run --env-file .env scripts/measure_cloud.py   # M1 to M3
uv run --env-file .env scripts/measure_local.py   # M4 to M6; needs a running container
uv run --env-file .env scripts/measure_local.py --cloud   # M4 to M6 in the cloud
```

## Where things are

- [docs/internal/plan.md](docs/internal/plan.md): the current phase, its tasks, and its stop
  conditions.
- [docs/internal/roadmap.md](docs/internal/roadmap.md): every phase and its status.
- [docs/contracts.md](docs/contracts.md): the record and the operations.
- [docs/guarantees.md](docs/guarantees.md): what a consumer can rely on, and the proof.
- [docs/internal/brief.md](docs/internal/brief.md): the design and its reasons.
- [CONTRIBUTING.md](CONTRIBUTING.md): the checks inside `make verify` and the test rules.

## Rules

- Work the current GitHub issue in task order, on one branch from `main`.
- Do not push, open a pull request, or post a comment until the repository owner agrees.
- Never name a private repository, a customer, or a deployment detail in a committed file.
  Before each commit, search the diff for the names that you know.
- Never write a measured number that you did not observe. Mark a claim that you read from
  source, and did not observe, as not observed.
- The measurement scripts read credentials from the environment. Do not put a key, a
  workspace id, or a database id in a file.
- If a change alters a behavior, update `docs/contracts.md` and `docs/guarantees.md` in the
  same commit.
