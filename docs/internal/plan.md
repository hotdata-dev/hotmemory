# Plan: phase 0, documents and measurements

Status: open, 2026-10-05. This file holds the current phase only and is replaced when the
phase closes. The phases themselves are in `roadmap.md`. Section numbers below refer to
`brief.md`.

## Goal

When this phase closes, a reader can learn the contracts and the guarantees from two public
files, a contributor can run one command that checks the documents, an engineer can run the
library's target against a RuntimeDB container on a laptop, agents working here have one
instruction file, and every guarantee marked to measure in the brief has a number and a
date.

## Tasks

GitHub issue #1 holds these tasks as a checklist. They are worked in order on one branch,
and one pull request closes the issue.

1. `docs/contracts.md`: sections 3 and 4 of the brief in public form, with the measured
   platform facts kept and no private name.
2. `docs/guarantees.md`: every row of section 5 with its state, and placeholders M1 to M6.
3. `README.md` revised to point at both files, and `CONTRIBUTING.md` with the one command
   and the rules from section 6. The make targets are named `verify` and `local-up`.
4. `Makefile` with `verify` running a link check over `README.md` and `docs/`, and a
   minimal `pyproject.toml` holding only the development tools so `verify` has something
   to run.
5. `docs/local.md` and `make local-up`: the container command from the RuntimeDB README,
   the three environment variables (`HOTDATA_API_URL`, `HOTDATA_WORKSPACE`, and the
   container's `RUNTIMEDB_AUTH__ALLOW_UNAUTHENTICATED=true`), and how to point the library
   at it.
6. `scripts/measure_cloud.py`: creates a throwaway database named by an environment
   variable, runs M1, M2, and M3, prints the numbers, deletes the database. Inline script
   metadata (PEP 723) so `uv run scripts/measure_cloud.py` works on its own.
7. `scripts/measure_local.py`: against a running container whose URL comes from the
   environment, runs M4, M5, and M6, prints the numbers. Same inline metadata. The script
   does not start the container.
8. `AGENTS.md` with the commands, the rules, and pointers to `plan.md`, `roadmap.md`,
   `contracts.md`, and `guarantees.md`, restating nothing they say. `CLAUDE.md` is one
   `@AGENTS.md` line plus any Claude-only note.
9. The numbers and dates written into `docs/guarantees.md`, and any guarantee the numbers
   contradict rewritten in the brief. The scripts are run by the repository owner: the
   cloud script needs an API key and a workspace, the local script needs Docker Desktop.

## Acceptance criteria

- AC1. M1 to M6 have numbers and dates in `docs/guarantees.md`. Proven by reading the
  file.
- AC2. `make verify` passes on a clean tree and fails when a link in `README.md` or under
  `docs/` is broken. Proven by breaking one link by hand and recording the failure in the
  pull request.
- AC3. `make local-up` starts a container that answers a query, with no API key and no
  control plane. Proven by the M6 script's first call.
- AC4. No file under `docs/` or at the root names a private repository, a customer, or a
  deployment detail. Proven by a grep for the known names, recorded in the pull request.
- AC5. `AGENTS.md` and `CLAUDE.md` exist, and `AGENTS.md` contains no sentence that
  `plan.md`, `roadmap.md`, `contracts.md`, or `guarantees.md` already contains. Proven by
  review.

## Verification

- Per commit: `make verify`.
- Before the pull request: the two measurement scripts, once each, with their output
  pasted into `docs/guarantees.md`.
- The cloud script costs a throwaway database for a few minutes. The local script costs
  nothing but time.

## Stop and ask if

- M1 shows an index does not serve rows loaded after its build. That changes section 3.4
  of the brief before phase 2 can start.
- M6 shows a managed-table call the driver needs does not work against the bare container.
  That decides whether local use needs a code path or only configuration.
- M5 shows the three-stage query is slower than a scan at every size.
