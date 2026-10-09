# Plan: phase 4, the first consumer

Status: agreed, 2026-10-09. This file holds the current phase only. The next phase replaces
it. The phases themselves are in `roadmap.md`. Section numbers below refer to `brief.md`.
Phase 3 closed with PR #9. `Memory`, the skill file, and the filter-first vector rankings
are on `main`.

## Goal

When this phase closes, the first consumer, an incident investigator built on Hotdata,
uses hotmemory in its own repository. It loads past post-mortems into a memory database.
At the start of an investigation, it recalls from that memory. A replay case runs an
incident of a class that two earlier post-mortems describe. With memory, the first round
names a prior incident of that class. Without memory, it does not. The survey (section 2.8) names this as the first proof of the
library.

The phase spans two repositories, with one pull request in each. This file plans both
halves and gives the detail of the hotmemory half. The consumer's issue gives the detail of
its own half, and this file never names that repository.

1. The hotmemory pull request adds a document loader to `Memory`, and keeps episodes out
   of `recall` and `profile`. It merges first.
2. The consumer's pull request pins hotmemory to the merge commit of the first pull
   request. It adds the post-mortem loader, the recall at investigation start, and the
   replay case.

Testing needs no release and no deploy. The replay runs the consumer's graph in a local
process against frozen data. The memory lives in the local stack or in a throwaway
database. hotmemory stays unpublished.

## Decisions

The owner agreed to all of these on 2026-10-09, after the consumer's agent answered the
questions of the first draft.

- The replay case (agreed). The case is an incident of a class that two earlier
  post-mortems describe: a node group that cannot grow, so a RuntimeDB pod stays Pending.
  The specific causes differ, and no person linked the case to the earlier incidents. So
  the case proves that a prior incident of the same class reaches the first round, and
  not that one cause repeated. The consumer's issue names the incidents. Its data is
  frozen with no expiry.
- The pass rule (agreed). The case is noisy, so each arm runs 3 times. With memory, at
  least 2 of 3 first-round reports name one of the two prior incidents by date or title.
  Without memory, none does. No evidence table holds those texts, so only memory can
  supply them.
- The cutoff (agreed). The replay uses no fact from after the incident start, in two ways.
  The consumer's loader loads only the post-mortems dated before the incident, and the
  recall passes `as_of` set to the incident start.
- The chunking (agreed). `Memory.load` cuts a document on Markdown headings first. It
  never cuts inside a fenced code block, because such a block can hold blank lines and
  lines that start with `#`. Then it packs the sections into chunks of at most
  `chunk_chars` characters (2,000 by default), and cuts a longer section on blank lines
  outside fences, then at that size. Each stored chunk starts with the first heading of
  the document and the heading path of the chunk, so that a chunk cut from a late section
  still says which document and section it belongs to.
- The loader (agreed). `Memory` gains `load(document, text, scope, extractor, actor,
  observed_at)`. It writes each chunk as a record of kind `episode` with the key
  `<document>-<n>`, where `n` counts from 1 with four digits. Then it calls the extractor
  on each chunk, as `capture` does. Each fact that comes back gets the id of its chunk in
  `sources`. It returns the ids of the episodes and of the facts. A second load of the
  same text writes nothing new, because the episode keys and the fact keys repeat.
- Episodes stay out of recall (agreed). `recall` and `profile`
  read facts, profiles, and procedures only. A fact names its episodes in `sources`, so a
  consumer reaches the evidence with `get`, or in one SQL join. For this, `Filter.kind`
  takes one kind or a tuple of kinds. In `HotdataStore`, a filter without `episode` reads
  only `memory_v1`. The filter key stays `kind`, and the wider type gets a changelog
  entry.
- The dependency (agreed). The consumer pins hotmemory to the GitHub archive URL of the
  merge commit, because its image builder has no `git`. Publishing to PyPI waits for a
  working version 1, as before.
- The setting (agreed). The recall at investigation start sits
  behind a setting of the consumer that defaults to off. A merge to the consumer's main
  branch builds an image but does not deploy it, so the pull request can merge without a
  change in behavior.
- One writer (agreed). In this phase, only the loader writes to
  the memory database, and it runs as a command, never inside an investigation. An
  investigation only reads. A capture after an investigation is later work, and needs the
  one-writer rule settled first.
- The consumer's shape (agreed). One namespace for incidents, with the affected
  component as the subject. The recall searches by the alert title and the first summary
  of the investigation, and runs beside the evidence load, so a cold engine start stays
  off the critical path. The block has a budget of 4,000 characters. The extractor writes
  facts that name the class of a failure as well as its specific cause, because the
  replay case depends on the class.

## Decisions taken during the work

The plan left these choices open. The owner can change any of them in review.

- `Filter.kind` keeps a single kind as a string, and turns a list into a tuple. An empty
  tuple raises an error, because it would match nothing.
- The counts in the block of `profile` leave episodes out too, because the block names
  what `recall` can reach. A namespace that holds only episodes shows a count of 0.
- `candidates` still searches every kind. The plan names only `recall` and `profile`.
- `chunk_chars` bounds the body of a chunk and does not count the prefix. The first
  heading of a post-mortem is a long sentence, so the prefix can reach about 250
  characters.
- The consumer's post-mortems have blocks over 2,000 characters that are tables and
  lists, not fences. So a block that does not fit is cut at line ends, and only a single
  line longer than `chunk_chars` is cut at that size. A cut pipe table repeats its header
  row and delimiter row in each later piece, at the consumer's request, so that the
  extractor keeps the column names.
- When a long section is cut into blocks, its heading stays with the block after it if
  both fit, so that no chunk holds a heading alone.
- The prefix is the first heading, then the heading path, with a heading equal to the one
  before it left out, so a path under the title does not repeat the title. A document
  with no heading gives chunks with no prefix.
- The extractor sees the chunk with its prefix, because the prefix tells it the document
  and the section.
- A fence starts with three or more backticks or tildes and ends with the same character
  at least as many times. A heading underlined with `===` or `---` is not a heading. The
  consumer's files have neither form.
- A document of blank lines writes nothing and returns two empty lists. A chunk for which
  the extractor returns no fact still gets its episode.
- A chunk whose episode is current with the same content is skipped, and the extractor is
  not called for it. Then a second load writes nothing new even when the extractor calls
  a model, and it costs no model call. The second load returns the episode ids and no
  fact ids.
- The facts of a chunk are written before its episode. If a load stops inside a chunk, the
  episode is missing, so the next load runs that chunk again.
- The facts are remembered chunk by chunk, as `capture` does, so the extractor of a later
  chunk sees the facts of the earlier chunks in its current records.
- An episode gets the chunk as content, the actor, and `observed_at`. It has no subject
  and no sources. A fact gets its own sources, then the id of its episode.
- `load` refuses a key that holds a fact before it calls the extractor for that chunk. The
  chunks before it stay written.
- A key number above 9999 gets more than four digits.
- An edited document writes the next revision of each changed episode. If it gets fewer
  chunks, its old last episodes stay current. The consumer loads each document once in
  this phase, so this waits for a later phase.

## Tasks

Worked in order. Tasks 1 to 6 are the hotmemory pull request. Tasks 7 to 11 are the
consumer's pull request, and its own issue gives their detail.

1. Close phase 3 in `roadmap.md` and replace `plan.md` with the phase 4 plan.
2. `Filter.kind` takes a tuple of kinds, in both drivers, with conformance tests and a
   changelog entry.
3. `recall` and `profile` leave episodes out, with tests over both drivers.
4. `Memory.load`, the chunking by headings and fences with the heading path, and the
   episode sources, with tests over both drivers.
5. A `load` script and example in the skill file, run by the command check.
6. The ledger rows for the loader and for the episodes in recall, and the docs audit.
7. Write the replay case and its pass rule into the consumer's eval suite. Its data is
   already frozen.
8. The post-mortem source and the extractor of the consumer.
9. The loader command of the consumer, run against a local or throwaway memory database.
10. The recall at investigation start, behind the setting, with its block in the first
    round of the investigation.
11. The replay case, run with memory and without memory.

## Acceptance criteria

- AC1. `make verify` passes offline in under five seconds.
- AC2. `make integration` passes against the local stack, and the CI integration job
  passes on the hotmemory pull request.
- AC3. `Memory.load` and the episode rule have tests that run against both drivers.
- AC4. A second load of the same text writes nothing new.
- AC5. The replay case passes by the pass rule above: at least 2 of 3 runs with memory
  name a prior incident of the class, and none of 3 runs without memory does.
- AC6. With the setting off, the consumer behaves as before.
- AC7. No public text names a private repository, a customer, or a deployment detail. A
  grep for the known names proves it over the files, the commit messages, the pull
  request, and the issue. The pull request describes the grep without the names.

## Answers from the consumer

The consumer's agent answered the questions of the first draft on 2026-10-09. The
decisions above take its answers. In short:

- The post-mortems are Markdown files with fixed sections and an appendix of shell
  commands. Most are 8,000 to 42,000 characters long. At 2,000 characters a chunk, a full
  load is about 110 extractor calls.
- The extractor fits the callable of `capture`. It calls a model that the consumer
  configures.
- The consumer needs no field that the record does not have. The episode key keeps the
  document name, and `observed_at` carries the incident date.

## Verification

- Per commit in hotmemory: `make verify`.
- Per commit that touches a driver: `make local-up`, then `make integration`.
- In the consumer: its own checks, and the replay case with memory and without memory.
- Before each pull request: the docs audit of the `pr-workflow` skill, and the grep for
  AC7.

## Questions left open

- After the engine fixes the filtered vector index search: let the vector rankings use
  the index again, and measure the gain.
- After the engine ships a conditional write: replace the one-process rule with a
  compare-and-set on the revision.
- A search over history, so that `recall(as_of=T)` can return a fact superseded after T.
- An exact count for each namespace in the block of `profile`.
- A capture after each investigation, which needs a second writer.

## Stop and ask if

- The consumer needs a field that the record does not have.
- A memory operation or the loader needs a new `Store` method.
- The replay case cannot tell a run with memory from a run without it.
- The frozen data of the replay case is lost to retention.
- The consumer needs to write to memory from inside an investigation.
