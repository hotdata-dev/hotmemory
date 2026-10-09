# Plan: phase 4, the first consumer

Status: draft, 2026-10-09. This file holds the current phase only. The next phase replaces
it. The phases themselves are in `roadmap.md`. Section numbers below refer to `brief.md`.
Phase 3 closed with PR #9. `Memory`, the skill file, and the filter-first vector rankings
are on `main`.

## Goal

When this phase closes, the first consumer, an incident investigator built on Hotdata,
uses hotmemory in its own repository. It loads past post-mortems into a memory database.
At the start of an investigation, it recalls from that memory. A replay case runs a repeat
incident twice. With memory, the cause of the prior incident reaches the first round.
Without memory, it does not. The survey (section 2.8) names this as the first proof of the
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

## Decisions this plan proposes

The owner has not agreed to these yet. Agree or change each one before task 2 starts.

- The loader. `Memory` gains `load(document, text, scope, extractor, actor,
  observed_at)`. It cuts `text` into paragraphs on blank lines. It packs whole paragraphs
  into chunks of at most `chunk_chars` characters (2,000 by default), and cuts a longer
  paragraph at that size. It writes each chunk as a record of kind `episode` with the key
  `<document>-<n>`, where `n` counts from 1 with four digits. Then it calls the extractor
  on each chunk, as `capture` does. Each fact that comes back gets the id of its chunk in
  `sources`. It returns the ids of the episodes and of the facts. A second load
  of the same text writes nothing new, because the episode keys and the fact keys repeat.
- Episodes stay out of recall. `recall` and `profile` read facts, profiles, and procedures
  only. A fact names its episodes in `sources`, so a consumer reaches the evidence with
  `get`, or in one SQL join. For this, `Filter.kind` takes one kind or a tuple of kinds. In
  `HotdataStore`, a filter without `episode` reads only `memory_v1`. The filter key stays
  `kind`, and the wider type gets a changelog entry.
- The dependency. The consumer pins hotmemory to a git commit. Publishing to PyPI waits for
  a working version 1, as before.
- The setting. The recall at investigation start sits behind a setting of the consumer
  that defaults to off. Its pull request can merge and ship without a change in behavior.
- One writer. In this phase, only the loader writes to the memory database, and it runs as
  a command, never inside an investigation. An investigation only reads. A capture after
  an investigation is later work, and needs the one-writer rule settled first.

## Tasks

Worked in order. Tasks 1 to 6 are the hotmemory pull request. Tasks 7 to 11 are the
consumer's pull request, and its own issue gives their detail.

1. Close phase 3 in `roadmap.md` and replace `plan.md` with the phase 4 plan.
2. `Filter.kind` takes a tuple of kinds, in both drivers, with conformance tests and a
   changelog entry.
3. `recall` and `profile` leave episodes out, with tests over both drivers.
4. `Memory.load`, the chunking, and the episode sources, with tests over both drivers.
5. A `load` script and example in the skill file, run by the command check.
6. The ledger rows for the loader and for the episodes in recall, and the docs audit.
7. Choose the replay case, and freeze its data before its retention window closes.
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
- AC5. The replay case passes as the consumer's issue defines it. With memory, the report
  of the first round cites the prior cause. Without memory, it does not.
- AC6. With the setting off, the consumer behaves as before.
- AC7. No public text names a private repository, a customer, or a deployment detail. A
  grep for the known names proves it over the files, the commit messages, the pull
  request, and the issue. The pull request describes the grep without the names.

## Questions for the consumer

The consumer's agent answers these before task 2, because the answers can change the
loader.

- Which incident is the replay case, and which earlier incident does its cause repeat?
  Where does its frozen data live? On what date does its retention window close?
- Where do the post-mortems live, in what format, and how many are there? How long is a
  typical one?
- What does the consumer's extractor need? Does it fit the callable of `capture`: the
  text, `observed_at`, and the current records, returning a list of `Fact`?
- What scope and subject fit the consumer? For example, a namespace for each service, and
  the alert or the service as the subject.
- Where in the graph does the first round start, and how big can the memory block be?
- What does a merge to the consumer's main branch trigger: an image build, a deploy, or
  both?
- Does the consumer need a field that is not in the record?

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
