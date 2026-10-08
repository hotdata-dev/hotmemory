# Plan: phase 3, the memory contract

Status: draft, 2026-10-08. This file holds the current phase only. The next phase replaces
it. The phases themselves are in `roadmap.md`. Section numbers below refer to `brief.md`.
Phase 2 closed with PR #7. `HotdataStore`, the conformance suite against both drivers, the
oracle test, and the CI integration job are on `main`.

## Goal

When this phase closes, `Memory` gives an agent the seven operations of section 4 over any
`Store`: remember, recall, candidates, supersede, forget, profile, and capture. Tests pin
the rendered blocks of `recall` and `profile` as exact strings. The skill file
`skills/hotmemory/SKILL.md` and its scripts exist, and `make verify` runs every command in
the skill file. The vector rankings of `HotdataStore` filter first, so a narrow scope keeps
its recall. Every ledger row that names phase 3 names a test.

## Decisions this plan takes

The owner agreed to all of them on 2026-10-08: the first four with the plan, and the rest
before task 2 started.

- Supersede (agreed). `put` and `Writer.put` gain `close_previous: bool = False`. The
  rule applies when it is True and the key has a current revision. Then the superseded
  row, which the same load already writes, also gets `valid_until` set to the new
  `valid_from`. Its `expired_at` is
  set to the clock's time. If the old `valid_from` is later than the new `valid_from`, the
  put raises `ValueError` and writes nothing. No extra load and no new `Store` method. The
  method set stays frozen, and the changed signature gets a changelog entry.
- Sources (agreed). Deduplication still compares normalized content. Sometimes the content
  matches but the put brings a source that the current revision lacks. Then the put writes
  a new revision whose `sources` are the current sources followed by the new ones, in
  order, without repeats. A put with the same content and no new source still writes
  nothing, so a retried `remember` stays safe.
- As-of (agreed). `recall(as_of=T)` searches current revisions only, and keeps the ones
  valid at T by the as-of rule. A fact superseded after T is not returned. The ledger row
  on `recall(as_of=T)` changes to say this. A search over history goes to the roadmap as
  deferred work.
- Recall under a narrow scope (agreed). Task 2 changes both vector rankings of
  `HotdataStore` to filter first and then rank by a scan, as `ranking="vector"` does. The
  BM25 ranking keeps its fetch depth, because `bm25_search` ranks the whole table. The
  vector indexes stay built. After the engine fixes the filtered index search, a later
  change can let the vector rankings use the index again.
- Keys (agreed). `remember` derives the key from the subject and the content. Each
  character of the subject outside `[A-Za-z0-9_-]` becomes `-`, and the result keeps its
  first 64 characters. Then come `-` and the first
  16 hex characters of the SHA-256 of the normalized content. An empty subject gives the
  key `fact-` and the hash. The derived key never contains `/` or `@`.
- Facts (agreed). `remember` takes a sequence of `Fact`, a frozen dataclass with `kind`,
  `content`, and the optional record fields. `Fact` joins `__all__`, with a changelog
  entry.
- The rendered block (agreed). `recall` and `profile` return a list of records and one
  text block. Each record is one line: `- <content> [sources: a, b] [valid: <from> to
  <until>]`. Dates are in ISO 8601, with `unknown` and `now` for null ends. The block holds
  whole lines only, and stops before the first line that passes the character budget. In
  the block of `profile`, a line `<kind>:` starts each group of records. The block never
  holds an instruction.
- Forget (agreed). `forget(ids=...)` deletes the key of each id, every revision.
  `forget(horizon=T)` deletes every key whose current revision has a `forget_after` before
  T. `Store` gains nothing. `forget(horizon)` lists the candidates and deletes them one key
  at a time, and `sweep` stays the fast path for a horizon of now.
- Profile counts (agreed). The block of `profile` ends with each namespace that the
  allowed scopes reach, with its count of current records. The count comes from `list`
  with a limit of 1000, and a count at the limit shows as `1000+`.
- Capture (agreed). The extractor is a callable. It takes the text, `observed_at`, and
  the list of current records that `recall` returns, and it returns a list of `Fact`.
  `capture` calls `remember` on the result. The tests pass a fake extractor.
- The skill file (agreed). `skills/hotmemory/SKILL.md` has a name and a description in
  front matter, and one example for each memory operation. `skills/hotmemory/scripts/`
  holds one command-line entry point for each operation. A script opens `HotdataStore`
  with `--database`. With `--memory-file`, it opens a `MemoryStore` that loads from and
  saves to a JSON file. The command check in `make verify` uses that file, so it keeps
  state across commands and needs no network.

## Tasks

Worked in order on one branch. Each task is one commit or a few.

1. Close phase 2 in `roadmap.md` and replace `plan.md` with the phase 3 plan.
2. The vector rankings of `HotdataStore` filter first and rank by a scan. Add a test where
   the scope holds 1 percent of the rows and every one of them must come back.
3. `close_previous` on `put` and `Writer.put`, in both drivers, with conformance tests and
   a changelog entry.
4. The merge of new sources into a new revision, in `_rules` and both drivers, with
   conformance tests. Update the ledger row on duplicates.
5. `Fact`, the key derivation, and `Memory.remember`.
6. `Memory.recall` and `Memory.candidates`, with the as-of rule and the character budget.
7. `Memory.supersede` and `Memory.forget`.
8. `Memory.profile`, with the counts of each namespace.
9. `Memory.capture`, with a fake extractor.
10. The rendered blocks pinned by tests, as exact strings, for both drivers.
11. The skill file, its scripts, and the command check in `make verify`.
12. The ledger: name the tests for the five rows that name phase 3. Add a row for each of
    these: `close_previous`, the merge of sources, the derived key, and the budget.
13. Docs: `README.md`, `docs/contracts.md`, `docs/guarantees.md`, `CONTRIBUTING.md`,
    `AGENTS.md`, and `CHANGELOG.md` audited against the code.

## Acceptance criteria

- AC1. `make verify` passes offline in under five seconds, with the command check.
- AC2. `make integration` passes against the local stack, and the CI integration job
  passes on the pull request.
- AC3. Every memory operation has a test that runs against both drivers.
- AC4. The rendered blocks of `recall` and `profile` match their pinned strings.
- AC5. In `HotdataStore`, a search whose scope holds 1 percent of the rows returns the
  top k rows of that scope.
- AC6. Every command in the skill file runs and exits zero in `make verify`.
- AC7. Every ledger row that names phase 3 names a test that passes.
- AC8. No public text names a private repository, a customer, or a deployment detail. The
  check covers committed files, commit messages, the pull request, and the issue. A grep
  for the known names proves it, and the pull request describes the grep without the
  names themselves.

## Verification

- Per commit: `make verify`.
- Per commit that touches a driver: `make local-up`, then `make integration`.
- Before the pull request: the docs audit of the `pr-workflow` skill, and the grep for
  AC8 over the files, the commit messages, and the text of the pull request.

## Questions left open

- After the engine fixes the filtered vector index search: let the vector rankings use
  the index again, and measure the gain.
- After the engine ships a conditional write: replace the one-process rule with a
  compare-and-set on the revision.
- A search over history, so that `recall(as_of=T)` can return a fact superseded after T.

## Stop and ask if

- A rendered block needs a field that the record does not have.
- `close_previous` cannot write the old and the new row in one load.
- The command check pushes `make verify` past five seconds.
- A memory operation needs a new `Store` method.
- The filter-first scan is slower than 1 second at 100,000 rows on the local stack.
