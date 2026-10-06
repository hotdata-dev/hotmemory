# Plan: phase 1, the storage contract, offline

Status: open, 2026-10-05. This file holds the current phase only. The next phase replaces it. The phases themselves are in `roadmap.md`. Section numbers below refer to
`brief.md`. Phase 0 closed with PR #2, and its results are in `docs/guarantees.md`.

## Goal

When this phase closes, the record and the `Store` protocol exist as typed Python. The
in-memory driver passes one conformance suite that proves each answered storage
guarantee. The four frozen surfaces and the ledger are guarded by tests. `make verify`
runs the full offline check in under five seconds, and CI runs the same target on every
pull request. Nothing in this phase touches the network.

## Decisions this plan takes

The brief leaves these open. The owner agreed to each one on 2026-10-05, and issue #4
repeats them.

- `MemoryStore` takes an embedder at construction: a callable from a list of texts to a
  list of vectors. `search` with query text and no embedder raises. The tests pass a
  deterministic fake embedder. This matches phase 0, which showed that fused retrieval on
  Hotdata needs an embedder from the caller.
- The store takes a clock at construction, a callable that returns the current UTC time.
  The default reads the system clock. The tests pass a fixed clock, so the suite needs no
  real time (section 6).
- The filter is a frozen dataclass with one optional field per allowed key. An unknown key
  cannot be expressed, and an unsupported value type raises.
- The package uses a `src/` layout with `py.typed`. It builds with hatchling, but this
  phase does not publish it. PyPI publishing comes after a working initial version.
- `Store` is a `typing.Protocol`, not an abstract base class. A driver conforms by its
  methods and needs no import from hotmemory. Logic that both drivers share, such as
  revision numbering, normalization, namespace matching, and filter checks, lives in plain
  functions that the drivers call.

## Tasks

Worked in order on one branch. Each task is one commit or a few.

1. Package layout: `src/hotmemory/` with `__init__.py` and `py.typed`. `pyproject.toml`
   turns packaging on, at version `0.0.0`, with no runtime dependencies. `mypy`, `pytest`,
   and `pytest-socket` join the dev group.
2. `make verify` runs ruff, ruff format in check mode, strict mypy over `src/` and
   `tests/`, the offline suite, and the link check, in that order (section 6.1).
   `CONTRIBUTING.md` lists the five steps.
3. The record: a frozen dataclass for schema version 1, with the fields and types in
   `docs/contracts.md`. It refuses a namespace label that contains `.`, derives `id`, and
   exposes the normalization that deduplication uses.
4. The `Store` protocol and the filter: `put`, `get`, `history`, `list`, `search`,
   `delete`, `list_namespaces`, and `writer`, with the filter keys in
   `docs/contracts.md`.
5. `MemoryStore`: revisions and supersession, exact deduplication on normalized content,
   whole-label namespace matching, `forget_after` hiding, cosine distance through the
   embedder, and a buffered `writer` that returns the ids it flushed (section 3.3).
6. The conformance suite: one test for each answered guarantee in `docs/guarantees.md`
   that concerns the storage contract, parametrized over drivers. Today the only driver is
   `MemoryStore`. Guarantees of the memory contract wait for phase 3.
7. The frozen surfaces: four tests that compare `__all__`, the `Store` method set, the
   record fields and types, and the filter keys against literal sets (section 6.3). Each
   failure message says that the change needs a changelog entry. `CHANGELOG.md` starts
   here, with an Unreleased section.
8. The ledger test: it reads `docs/guarantees.md`. A test named in the Test column that
   does not exist makes it fail (section 6.4). The Test column gets a name for each row that task
   6 proves. The other rows say which phase proves them.
9. CI: a GitHub Actions workflow that installs uv and runs `make verify` on each pull
   request and on each push to `main`.
10. Docs: `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, and `docs/contracts.md` audited
    against the code, with any decision above that changes a public statement written
    into `docs/contracts.md`.

## Acceptance criteria

- AC1. `make verify` passes on a clean tree in under five seconds and runs the five steps
  in order. Proven by its timed output in the pull request.
- AC2. A change to a frozen surface makes its test fail. Proven by removing one
  element from each surface by hand and recording the four failures in the pull request.
- AC3. A rename of a named test makes the ledger test fail. Proven by hand, recorded in
  the pull request.
- AC4. Every answered storage-contract row in `docs/guarantees.md` names a test that
  passes.
- AC5. The offline suite passes with sockets disabled. Proven by `pytest-socket`'s
  `--disable-socket` flag in `make verify`.
- AC6. CI runs `make verify` on the pull request and passes.
- AC7. No committed file names a private repository, a customer, or a deployment detail.
  Proven by a grep for the known names, recorded in the pull request.

## Verification

- Per commit: `make verify`.
- Before the pull request: the hand checks for AC2 and AC3, and the name grep for AC7.
- This phase needs no credentials and no Docker.

## Questions this phase leaves open

- The oracle comparison. The brief makes `MemoryStore` the oracle for `HotdataStore`: a
  test builds the same records in both drivers and compares their `search` results.
  `MemoryStore` matches the engine's cosine distance exactly, but it cannot reproduce the
  engine's BM25 scoring. So an exact comparison works only on the vector-only path. For the
  fused search, the test has to compare something looser, such as the set of records
  returned. Phase 2 decides which, and this phase keeps `MemoryStore` search vector-only so
  that either choice stays open.
- How `supersede` closes the old record. The memory contract sets the old record's
  `valid_until` and `expired_at`. `Store.put` sets only `superseded_by` on the old
  revision, and no `Store` operation can change another field of a written revision. Phase
  3 decides how `Memory` writes those two fields, and the change to `Store` needs a
  changelog entry.
- Deduplication compares content only. A `put` that keeps the content and adds a source,
  a tag, or a new `forget_after` writes nothing. The contract counts sources as
  corroboration, so phase 3 decides how `remember` adds a source to an existing fact.

## Stop and ask if

- A guarantee marked answered cannot be given a test that observes it against
  `MemoryStore`.
- A frozen surface needs to differ from `docs/contracts.md`.
- `docs/contracts.md` leaves an operation's behavior ambiguous, for example whether `list`
  takes an as-of time, and the choice changes a public statement.
- `MemoryStore` needs a field that is not in the record.
- `make verify` cannot stay under five seconds without tiers.
