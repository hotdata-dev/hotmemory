# Plan: phase 2, the Hotdata driver

Status: open, 2026-10-08. This file holds the current phase only. The next phase replaces it.
The phases themselves are in `roadmap.md`. Section numbers below refer to `brief.md`. Phase 1
closed with PR #5. The storage contract, `MemoryStore`, and the conformance suite are on
`main`.

## Goal

When this phase closes, `HotdataStore` implements the `Store` protocol over one managed
database. The conformance suite runs against both drivers. The suite runs against the local
RuntimeDB stack in CI, and an oracle test compares `HotdataStore` with `MemoryStore`. A
caller can provision a database by name, can embed with OpenAI or with an own embedder, and
can sweep records past `forget_after`. `make verify` stays offline and under five seconds.

## Decisions this plan takes

The owner agreed to each one on 2026-10-08, and the issue repeats them.

- Provisioning. `HotdataStore.provision(name, ...)` is safe to run again. It looks up the
  databases with that name. If it finds exactly one with the expected tables and layout, it
  opens that one. If it finds none, it creates the database, declares the tables, and builds
  the BM25 indexes. If it finds more than one, or one with a different layout, it raises and
  creates nothing. Two processes that provision the same new name at the same moment can
  both create a database, because the platform has no conditional create. The docs state
  this race, and the driver does not try to prevent it. A caller can also open a database
  by its id.
- The layout. `provision` declares three tables: `memory_v1`, `episode_v1`, and a one-row
  `meta_v1` that records the schema version, the embedding model name, and the vector
  size. `memory_v1` and `episode_v1` have the record columns and two embedding columns,
  `content_embedding` and `cues_embedding`. The cues vector embeds the cues joined with
  newlines. When a record has no cues, the cues vector is null. Vectors are float32 lists, the same as the
  `hotdata-langchain` vector store. The layout is permanent, so the embedding model and the
  vector size are fixed for each database. A store refuses an embedder whose vectors have
  another size.
- Embedding. Fused search needs BM25 and plain vector indexes on one table, and a
  provider-backed index refuses to share a table (M5). So the client embeds, and the rows
  carry the vectors. RuntimeDB has no call that embeds text and returns the vector. Only
  the routes that manage embedding providers exist. This was read from the RuntimeDB source
  at `4615bd3` on 2026-10-08 and was not observed. The library ships `OpenAIEmbedder` in an
  optional extra, `hotmemory[openai]`. Any callable that matches `Embedder` also works. A
  LangChain `Embeddings.embed_documents` method already matches it.
- Writers. One `HotdataStore` holds a lock around the read and the load of each write.
  Across processes, two writers on one key can both compute the same next revision, and
  the later load replaces the earlier row. The docs state that one process writes to a database. The fix waits for
  a conditional write in RuntimeDB, an `UPDATE ... WHERE` that reports how many rows it
  changed. The ledger row on two writers says that it holds inside one process only.
- Episodes. A `put` with `kind="episode"` writes to `episode_v1`. Every other kind writes
  to `memory_v1`. `get`, `history`, `list`, `search`, `delete`, and `list_namespaces` read
  both tables. `Filter(kind="episode")` searches episodes only. A key cannot change between
  `episode` and another kind. In both drivers, a `put` that changes a key across that
  line raises an error.
- The sweeper. `sweep()` joins the `Store` protocol. It deletes every revision of each key
  whose current revision is past `forget_after` at the clock's time, and returns the ids
  that it deleted. Both drivers implement it. The change to the frozen method set gets a
  changelog entry.
- The oracle. `MemoryStore` cannot reproduce the engine's BM25 scores, because the engine
  uses its own tokenizer and constants. Cosine distance is arithmetic, so the vector-only
  ranking compares exactly: the same records in the same order, with distances equal
  within 1e-6, because the engine stores float32. The fused search compares more loosely:
  on a fixture where k covers every record, both drivers return the same set of records.
- Dependencies. `hotmemory[hotdata]` adds `hotdata-framework` and `pyarrow`.
  `hotmemory[openai]` adds `openai`. The core package keeps no runtime dependencies.
  `HotdataStore` lives in `hotmemory.hotdata` and `OpenAIEmbedder` in `hotmemory.openai`,
  outside the top-level `__all__`, so `import hotmemory` needs neither extra.
- Latency. A cloud load costs about 2.1 seconds and a local load about 24 ms (M4). This
  phase accepts that cost.

## Tasks

Worked in order on one branch. Each task is one commit or a few.

1. Close phase 1 in `roadmap.md` and replace `plan.md` with the phase 2 plan.
2. Packaging: the `hotdata` and `openai` extras, the `hotmemory.hotdata` module, and
   `HotdataStore.provision` and opening by id, with the three tables and the BM25 indexes.
   Check first that `hotdata-framework` can list databases by name, declare the layout,
   and read it back.
3. The write path: `put` reads the current revision and writes the new row and the
   superseded old row in one upsert load. Deduplication, the episode routing, and the key
   rule for kinds come from `hotmemory._rules`. The lock holds across the read and the
   load. A load that gets `409 RESOURCE_LOCKED` retries up to 8 attempts, with a backoff
   from 0.25 seconds that doubles to at most 4 seconds (M3). `writer` sends one load per
   flush. `delete` is a keyed delete load of every revision.
4. The read path: `get`, `history`, `list`, and `list_namespaces` in SQL, with every
   `Filter` key and the null rule for ranges. A prefix matches whole labels on the stored
   path string, with any `%`, `_`, or escape character in a label escaped.
5. The retrieval query (section 3.4): the exact filters, then BM25 over `content` and
   vector distance over `content_embedding` and `cues_embedding`, fused by reciprocal rank
   with the constant 60, in one SQL query. The BM25 fetch depth is wide enough to survive
   the filters. The vector and sorted indexes are an optimization (section 3.4).
6. The sweeper: `sweep()` on `Store`, `MemoryStore`, and `HotdataStore`, a conformance
   test, the ledger row, and a changelog entry.
7. `OpenAIEmbedder` in `hotmemory.openai`, tested with a fake client and no network.
8. The integration leg: the `store` fixture gains a `hotdata` driver that provisions a
   database with a new name for each run and deletes it at the end. It skips unless
   `HOTMEMORY_TEST_URL` names a running engine. `make integration` runs the suite against
   the local stack. A second CI job starts the stack with `make local-up` and runs
   `make integration`. `make verify` stays offline.
9. The oracle test, as decided above, with the deterministic fake embedder.
10. The ledger: name the tests for the phase 2 rows (the two-process 409 row and the
    sweeper half of the `forget_after` row). Narrow the row on two writers to one process.
    Add a row for each of these: the provisioning rerun, the one-process rule, the episode
    routing, and the sweeper. Also add a row on whether a plain vector index serves rows
    loaded after its build.
11. Docs: `README.md`, `docs/contracts.md`, `docs/guarantees.md`, `docs/local.md`,
    `CONTRIBUTING.md`, `AGENTS.md`, and `CHANGELOG.md` audited against the code. The
    contracts state provisioning and its race, the one-process rule, the embedding
    columns, the episode routing, and `sweep`.

## Acceptance criteria

- AC1. `make verify` passes offline in under five seconds. Proven by its timed output in
  the pull request.
- AC2. `make integration` runs the whole conformance suite against `HotdataStore` on the
  local stack, and every test passes. Proven by its output in the pull request.
- AC3. CI runs the integration job on the pull request, and it passes.
- AC4. The oracle test passes: the vector-only ranking matches exactly within 1e-6, and the
  fused search returns the same set.
- AC5. A second `provision` with the same name returns the same database. A duplicate name
  or a different layout raises and creates nothing. Proven by integration tests.
- AC6. The frozen-surface tests pass, and each surface change has a changelog entry.
- AC7. Every row in `docs/guarantees.md` that names phase 2 now names a test that passes.
- AC8. No committed file names a private repository, a customer, or a deployment detail.
  Proven by a grep for the known names, recorded in the pull request.

## Verification

- Per commit: `make verify`.
- Per commit that touches the driver: `make local-up`, then `make integration`.
- Before the pull request: the AC5 checks and the name grep for AC8.
- The cloud needs credentials, and only the owner runs it, from a gitignored `.env`.

## Questions left open

- For phase 3: `supersede` sets `valid_until` and `expired_at` on the old record, and no
  `Store` operation can change those fields today.
- For phase 3: deduplication compares content only, so a `put` that adds a source to an
  existing fact writes nothing. The contract counts sources as corroboration.
- After RuntimeDB ships a conditional write: replace the one-process rule with a
  compare-and-set on the revision.

## Stop and ask if

- The engine refuses a planned part of the layout, for example a BM25 index on an empty
  table, or two plain vector indexes on one table.
- `hotdata-framework` cannot list databases by name or read back a table layout.
- A plain vector index does not serve rows loaded after its build.
- A conformance test fails against `HotdataStore`, and the fix needs a contract change.
- The oracle's exact comparison differs by more than 1e-6.
- The whole-label prefix match cannot be written safely in SQL.
- The integration job cannot start the local stack in CI within ten minutes.
- `make verify` cannot stay under five seconds without tiers.
