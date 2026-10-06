# Brief: hotmemory, agent memory as tables on Hotdata

Status: draft, revised 2026-10-05, phases moved to `roadmap.md` and `plan.md`, and the phase 0
measurements applied (results in `docs/guarantees.md`). Phase 1 built the storage contract with
`MemoryStore`, and `docs/contracts.md` states the details that it fixed. It fixes the two
contracts, the guarantees behind them, and the harness that proves them, before the first
line of code. The revision applies section 4 of `survey.md`, in this folder.

Evidence labels. A sentence marked [measured] was run against a real Hotdata workspace and
the number is transcribed. A sentence marked [to measure] names a question that the first
phase answers. Anything unmarked is a design decision.

This folder is internal in audience, not in visibility. Nothing in it names a private
repository, a customer, or a deployment detail. The first consumer is an incident
investigator built on Hotdata, and it is named only that way here.

## Where the work is tracked

This brief holds the design and its reasons. The phases, their status, and anything
deferred are in `roadmap.md`. The current phase, with its tasks, acceptance criteria,
measurements, and verification, is in `plan.md`. Both files are in this folder. A task
becomes a GitHub issue only when its phase is the current one.

## 1. Goal

A Python library that stores an agent's long-term memory as rows in a Hotdata managed table,
and gives any agent five operations over them: put, get, list, search, and delete. On top of
that storage, a memory contract gives an agent the operations it uses in practice: remember,
recall, supersede, forget, and profile. The incident investigator is the first consumer. The
library is not specific to it.

The one claim that makes this a product and not a feature: a memory table is an ordinary
table, so an agent can join its memory to its own data in one SQL query. The README leads
with that sentence. The survey confirms that no surveyed system stores memory as typed
columns with validity spans in the same engine as the consumer's data.

## 2. Context

### 2.1 Where the package sits

The Python stack has four layers. The `hotdata` package is the generated HTTP client.
`hotdata-framework` is the hand-written runtime over it, and it owns managed databases,
parquet upload and load, and index builds. `hotdata-langchain` adds LangChain tools and a
VectorStore. Agent applications sit on top. `hotmemory` sits directly over
`hotdata-framework` and imports nothing from `hotdata-langchain`. Adapters for LangGraph and
for MCP are separate work and are listed under out of scope.

The library runs inside the consumer's process. The consumer imports it, and it calls the
Hotdata API over HTTPS with the consumer's API key. There is no hotmemory server. The hosted
API that production needs already exists, and it is Hotdata's own API: files, loads,
indexes, and query. The same library runs against a local RuntimeDB on a laptop, but
not against the bare engine image. [measured, M6] A managed table needs a Postgres catalog,
because DuckLake metadata lives only in Postgres, and storage that can presign, because the
framework uploads parquet through presigned URLs. The local stack in `docs/local.md` is
three containers: Postgres, RustFS, and the engine sharing the RustFS network namespace so
the presigned address resolves on both sides. The library then needs `HOTDATA_API_URL`
pointing at the engine, `HOTDATA_WORKSPACE` set to any id, and any non-empty
`HOTDATA_API_KEY`. Every driver call works on that stack except a provider-backed vector
index, because no embedding provider is configured locally. A separate memory service in front of Hotdata is what section 8 rules
out, and the reasons it can become necessary are listed there.

### 2.2 Platform facts that shape the design

These come from measurements against Hotdata between 2026-08-27 and 2026-09-18.

- A write is a file load in every released engine and in production. The caller uploads
  parquet and calls a keyed load in replace, append, upsert, update, or delete mode.
  [measured] On RuntimeDB main since 2026-09-25, a SQL write path exists behind the
  `[engine] sql_writes` flag, off by default, and production does not set it. INSERT and
  CREATE TABLE, CREATE TABLE AS, and DROP TABLE are implemented. An INSERT is an append
  load whose source is a query: the planned rows are staged as parquet and appended through
  the same locked load path, so it holds the same per-table lock and maintains indexes the
  same way. A SQL write needs the read and write claims and can target only the database's
  own catalog.
- A write costs about 2.1 seconds per call and is flat in row count. One row costs 1.75
  seconds, ten thousand rows cost 2.5 seconds. Batching is free. The only lever is call
  count. [measured]
- There is no conditional write. No mode takes a predicate on the existing value and none
  returns the affected row. Two writers on one key is last writer wins. [measured] This is
  the constraint that will change: UPDATE and DELETE over row ids are open on RuntimeDB,
  and an UPDATE with a WHERE clause that returns `rows_affected` is a conditional write.
  The design below assumes today's engine.
- A load holds an exclusive lock on the whole table. A second concurrent load is refused
  with `409 RESOURCE_LOCKED`. Distinct keys buy no concurrency. [measured]
- Read after write was never stale across 30 loads. A read costs 0.3 to 0.6 seconds. A cold
  engine costs about 8 to 10 seconds on its first call. [measured]
- A table layout is permanent. There is no ALTER. A `mode="upsert"` load with extra columns
  succeeds and silently drops them. Only a `mode="replace"` load widens a table. [measured]
- `delete_managed_table` burns the table name forever. [measured]
- `expires_at` on a database records a lifetime and nothing enforces it. [measured]
- A managed database is a hard isolation boundary. Managed databases cannot attach each
  other. A registered Postgres connection attaches cleanly. [measured]
- Three kinds of index exist on a managed table: BM25 over a text column, vector over a
  float-list column or over a text column with server-side embedding, and sorted. A vector
  index has two modes. Plain mode indexes a vector column the caller embedded. Provider-
  backed mode takes a text column, embeds it on the server through a Hotdata embedding
  provider, and the query passes text. A metric mismatch in plain mode silently reverts to
  a full scan. Indexes are invisible to SQL, so only the control plane can say whether one
  exists. (`hotdata_framework/client.py:584-600`, `hotdata_langchain/vectorstore.py:541`)
- A provider-backed vector index cannot share its table with any other index. The engine
  refuses the second one. A BM25 index, a plain vector index, and a sorted index share a
  table. [measured 2026-08-18, confirmed by M5 on 2026-10-05]
- `bm25_search` refuses to run on a column without a BM25 index. There is no scan
  fallback. [measured, M5]
- RuntimeDB runs on a laptop with a Postgres container and an S3-compatible storage
  container, with no cloud dependency. The bare image, with its SQLite catalog and
  filesystem storage, refuses managed tables. [measured, M6] The local stack is a real
  Hotdata for development and for measurements that production cannot run. A load there
  costs about 24 ms, against about 2.1 seconds in the cloud, so the cloud write cost is
  outside the engine. [measured, M4]

### 2.3 What the design takes from these facts

- Records are immutable. A revision is a new row that marks the old row superseded. This
  is the only way to get history, supersession, and safe concurrency from a store whose
  write primitive is a keyed row replace.
- One writer per table. The driver serializes loads and retries on 409.
- Writes are either synchronous and slow, or buffered and flushed. The API exposes both and
  the guarantee section says which is visible when.
- Expiry is a column plus a sweeper. Nothing is deleted by the platform.
- The tenant boundary is a database, enforced by the platform through the API token's
  workspace allow-list. A namespace is a column filter inside the database, enforced by
  the library. The library never claims to enforce more than that.
- The schema is versioned in the table name. A column change is a new version, never an
  edit.
- Granularity is one fact per row. A document is not a memory. It is an episode that facts
  point back to. Section 3.4 says why.

## 3. The storage contract

The storage contract is the public, stable surface. It is a Python protocol named `Store`.
Every driver implements it and the conformance suite runs against every driver.

### 3.1 The record

A record is the unit the store holds. Its fields are fixed for schema version 1.

| Field | Type | Meaning |
|---|---|---|
| `namespace` | tuple of strings | Where the record lives. Stored as a path string joined with `/`, and matched on whole labels, never on a string prefix. A label is not empty and contains no `.` and no `/`. |
| `key` | string | The caller's stable identifier inside the namespace. It is not empty and contains no `/` and no `@`. |
| `revision` | integer | 1 for the first put under a key, then counting up. |
| `kind` | string | One of `fact`, `profile`, `procedure`, `episode`. |
| `subject` | string | What the record is about, for example an alert key, a person, a service. Empty when unknown. |
| `content` | string | The text a model reads. Not empty after normalization. This is the column a provider-backed index embeds. |
| `cues` | tuple of strings | Questions or phrases this record answers. Optional. Embedded separately from `content`. Section 3.4. |
| `payload` | JSON object | Structured data the consumer defines. The store never reads it. |
| `tags` | tuple of strings | Free labels. Filterable. |
| `sources` | tuple of strings | References to where the record came from: a thread id, a document path, a run id, an episode key. The length of this list is the corroboration count. |
| `actor` | string | Who wrote this revision: a user id, an agent name, an extractor name. |
| `created_at` | timestamp | When this revision was written. System clock. |
| `observed_at` | timestamp or null | The time of the source the record came from. A post-mortem loaded a year later keeps the incident date here. |
| `valid_from` | timestamp or null | When the fact became true. Defaults to `observed_at`. Null means unknown. |
| `valid_until` | timestamp or null | When the fact stopped being true. World clock. Null means still true. |
| `expired_at` | timestamp or null | When the store learned that the fact stopped being true. System clock. Null on a current record. |
| `superseded_by` | string or null | The id of the revision that replaced this one. Null on the current revision. |
| `forget_after` | timestamp or null | When a sweeper is allowed to delete the record. Null means keep. |
| `forget_reason` | string | Why, when `forget_after` is set. Empty otherwise. |
| `id` | string | `namespace/key@revision`. Derived. The load key. |

The record carries no embedding field in the public contract. The Hotdata driver adds an
embedding column when the caller supplies an embedder, and omits it when the caller uses a
provider-backed index. Which one is in use is driver configuration.

Two clocks, on purpose. `valid_until` is when the world changed. `expired_at` is when the
store found out. A replay that asks what memory held on a given day reads `created_at` and
`expired_at`. A question about the world on that day reads `valid_from` and `valid_until`.

### 3.2 The operations

| Operation | Signature, in words | Behaviour |
|---|---|---|
| `put` | namespace, key, record fields | Writes a new revision. If the key exists, the new row gets the next revision and the previous current row gets `superseded_by` set. Returns the id. If the normalized content equals the current revision's, writes nothing and returns the current id, unless the current revision is past its `forget_after`. |
| `get` | namespace, key, optional revision | Returns the current revision, or the named one. Returns None when absent. |
| `history` | namespace, key | Returns every revision, oldest first. |
| `list` | namespace prefix, optional filter, optional since, limit | Returns current revisions under the prefix, newest first. No model, no embedding. The filter is equality on `kind`, `subject`, `tags`, and `actor`, and a range on `valid_from`, `valid_until`, `created_at`, and `expired_at`. |
| `search` | query text or none, namespace prefixes, optional filter, k | Returns up to k current revisions ranked by relevance, closest first, with a distance. The same filter as `list`. With no query text it is `list`. |
| `delete` | namespace, key | Removes every revision of the key. Hard delete. |
| `list_namespaces` | optional prefix | Returns the distinct namespaces under the prefix. |
| `writer` | optional row count, optional interval | A context manager. Every `put` inside it is buffered. The buffer flushes on exit, or at a row count, or at an interval, and the writer records the ids it flushed. |

Deleted revisions, superseded revisions, and records past `forget_after` never appear in
`list` or `search`. `history` shows superseded revisions. Nothing shows deleted ones.

Normalized content means lowercased with whitespace collapsed. Deduplication is exact on
that form and nothing more. A paraphrase is a new record. Merging paraphrases is the
extractor's job, with the help of `candidates` in section 4.

### 3.3 The drivers

Two drivers ship in version 1.

- `MemoryStore`. In process, in memory. It is a real driver and not a mock. It computes
  relevance with the same cosine distance the engine uses, and it refuses any filter it
  does not model. The offline suite runs against it, and it is the oracle for the other
  driver.
- `HotdataStore`. One managed database, two tables per schema version, keyed loads, a
  serialized writer, and the ranking query in section 3.4. The integration leg runs against
  it. Against the local RuntimeDB stack it is also the development driver, so no third
  driver is needed for working offline. The integration leg can run against that stack in
  CI, with plain vector indexes in place of provider-backed ones. [measured, M6]

### 3.4 Tables and retrieval

Two tables per schema version, because facts and raw material have different shapes and
different write patterns.

The `memory` table holds facts, profiles, and procedures: small rows, revisioned, searched
often, and the rows that `profile` renders. The `episode` table holds raw material: a
thread, a document, a post-mortem, chunked at a fixed size, append-only, never revisioned,
and searched when a fact is not enough. A fact's `sources` names the episode keys it came
from, so a consumer can walk from a fact to its evidence in one join. Tabular data is never
copied into either table. It stays in the consumer's own tables and a fact points at it.

Why one fact per row and not one document per row. A document stored as one record makes
every small revision a near-duplicate of the whole, with a cosine distance close to zero
between versions, which is exactly the duplicate problem a memory layer exists to avoid.
Small facts revise cleanly, supersede cleanly, and render into a profile. The surveyed
systems that extract agree on the unit: 15 to 80 words in mem0, one edge per fact in
Graphiti. Markdown remains a fine format for the `content` column of an episode chunk.

Retrieval runs in one SQL query over the `memory` table, in three stages.

1. Exact filters first: namespace labels, validity at the as-of time, `kind`, `subject`,
   `tags`, and `forget_after`. These are plain predicates and cut the candidate set before
   any ranking.
2. Three rankings over the survivors, each served by its own index: BM25 over `content`,
   vector distance over `content`, and vector distance over `cues`. Fused by reciprocal rank
   fusion, written as plain SQL with common table expressions. The engine has no fusion
   primitive and needs none. Because a provider-backed index excludes every other index,
   the two vector rankings need plain vector indexes over embedding columns, and so an
   embedder supplied by the caller. Without an embedder, the driver can offer BM25 alone or
   a provider-backed vector ranking alone, not both. `bm25_search` ranks the whole table
   and the filters of stage 1 apply after it, so the BM25 fetch depth must be wide enough
   to survive the filters.
3. A sorted index on `created_at` for recency ordering and for the sweeper.

`cues` is the part that is not in any surveyed system and is cheap here. A cue is the
question a record answers, written at capture time by the extractor or by the caller. A
query that resembles the question matches the cue even when it shares no words with the
content. Cues are optional, and the content-only path always works, so a consumer that
writes no cues loses nothing it had.

Fast reads come from the table being small, not from cleverness. A consumer's memory table
holds thousands of rows, and a filtered scan of that is fast before any index exists. M5
measured the crossover. In the engine, the vector index starts to pay between one thousand
and ten thousand rows, and at one hundred thousand rows it cuts the filtered vector rank
from 48 ms to 15 ms. The fused query with indexes takes 21 ms there. In the cloud, one
request costs about 400 ms at every size, and that floor hides the saving up to at least
one hundred thousand rows. Only the sorted index on `created_at` showed a cloud gain, 695 ms
to 407 ms at one hundred thousand rows. The driver therefore builds the BM25 index whatever
the size, because `bm25_search` needs it, and treats the vector and sorted indexes as an
optimisation that matters from about ten thousand rows. [measured, M5]

## 4. The memory contract

The memory contract is what an agent calls. It is a class named `Memory` built over one
`Store`. It is the surface the first consumer uses and the surface a plain Python agent
uses. It can change more freely than the storage contract while the library is young.

| Operation | Signature, in words | Behaviour |
|---|---|---|
| `remember` | facts, scope, actor | Writes already-structured facts. Each fact is a record with `kind` set. A deterministic key is derived from the subject and the normalized content hash, so a retried call writes nothing new. |
| `recall` | query, scopes, optional as_of, budget in characters | Searches the allowed scopes, keeps records valid at `as_of`, and returns the top records inside the budget as a list and as one rendered block. The block labels every record with its sources and its validity span, and nothing else. |
| `candidates` | fact, scopes, k | Returns the k nearest current records with their distances. No decision. A caller's consolidator reads this before deciding to `remember` or `supersede`. |
| `supersede` | key of the record to close, new fact, optional valid_from | Closes the named record and writes the new fact as the next revision under its key. The old record's `valid_until` becomes the new record's `valid_from`, and its `expired_at` becomes now. If the old record's `valid_from` is later than the new one's, the call refuses. The library decides nothing by itself. |
| `forget` | ids, or a horizon | Deletes the named records, or every record whose `forget_after` is before the horizon. |
| `profile` | subject, scopes, budget in characters | Returns the current records whose `subject` matches, grouped by `kind`, as one rendered block under the budget. The block ends with the namespaces and record counts that `recall` can reach, so an agent knows what to search for. This is the always-loaded block. |
| `capture` | text, scope, actor, observed_at, extractor | Calls the caller's extractor with the text, `observed_at`, and the current records that `recall` returns for the scope, then calls `remember` on what comes back. The extractor is a plain callable. The library ships no model and names no model. |

The as-of rule, stated once. A record is valid at time T when `valid_from` is null or at
most T, and `valid_until` is null or after T. A null `valid_from` means the start of time.
A conformance test pins both halves.

Three rules hold across every operation.

- A scope is a namespace prefix. The caller supplies the allowed scopes on every call.
  The library filters on them and does nothing else to enforce access.
- Memory is a record, not a rule. The rendered block from `recall` and from `profile` says
  what was recorded and when. It never says what to do. A consumer that wants instructions
  writes them in its own prompt.
- A write is visible to the next `recall` and never inside the current turn. A consumer
  that captures and recalls in one step reads what was there before the capture.

## 5. Guarantees

Each guarantee names its state. Answered means a measurement already supports it. To
measure means phase 0 must answer it before phase 1 relies on it. Every answered guarantee
gets a conformance test in phase 1, and `docs/guarantees.md` names the test.

| Question | Answer | State |
|---|---|---|
| Does a second `put` under the same key replace the record? | No. It writes revision n+1 and marks revision n superseded. `get` returns n+1. | answered |
| Can a retried `remember` create duplicates? | No. The key is derived from subject and normalized content hash, and a put whose normalized content equals the current revision's writes nothing. | answered |
| Is a paraphrase a duplicate? | No. Deduplication is exact on normalized content. `candidates` exists so a caller can decide. | answered |
| When a synchronous `put` returns, is the record visible to `list`? | Yes. Read after write was never stale in 30 trials. | answered [measured] |
| When a synchronous `put` returns, is the record visible to `search`? | Yes. Without an index the ranking query scans the table. With a provider-backed vector index or a BM25 index, the first search after the load returned the new row. | answered [measured], M1 |
| When a buffered `put` returns, is the record visible? | No. It is visible after the writer flushes, and the writer records the ids it flushed. | answered |
| What happens when two processes write the same table? | The second load is refused with 409. The driver retries with backoff and gives up after a bound. Within one process the writer serializes. | answered [measured], M3: with two concurrent writers no load needed more than 3 of 8 attempts |
| What happens when two writers put the same key? | Last writer wins at the row level. Because revisions are new rows, both revisions exist and the later one is current. | answered |
| Does `delete` remove retained revisions and derived embeddings? | Yes. The embedding is a column of the row, and after a keyed delete neither a provider-backed vector index nor a BM25 index returned the row. | answered [measured], M2 |
| Which filters work in `list` and `search`? | Equality on `kind`, `subject`, `tags`, and `actor`. Range on `valid_from`, `valid_until`, `created_at`, and `expired_at`. Prefix on namespace labels. Anything else raises. | answered |
| Does a higher score mean more relevant? | The store returns distance, and lower is closer. The memory contract returns records in order and no score. An adapter that needs a score converts. | answered |
| What does `recall(as_of=T)` return? | Records valid at T by the as-of rule in section 4, including records superseded after T when `history` is consulted, and excluding them otherwise. | answered |
| Who enforces scope? | The library filters on the caller's allowed scopes. The platform enforces the database boundary through the API token. A caller holding the token can bypass the library. | answered |
| Can a consumer tell sources from extractions from hypotheses? | Yes through `kind`, `sources`, and `actor`. An extracted fact carries the extractor's name in `actor`. | answered |
| Is a record deleted when `forget_after` passes? | No. It stops appearing in `list` and `search`. A sweeper deletes it when run. | answered [measured] |
| Does a write inside a turn reach a `recall` in the same turn? | No, by contract. A consumer reads what was there before its own capture. | answered |

Phase 0 measurements. M1 to M3 run against a throwaway database that the run creates and
deletes. M4, M5, and M6 ran against throwaway cloud databases and against the local stack,
because the bare container refuses managed tables. All six were run on 2026-10-05, and
`docs/guarantees.md` holds the numbers.

- M1. Build a provider-backed vector index on a table, load ten more rows, and search
  for one of them. Record whether the new row is served, and after how long.
- M2. Delete a row that an index covers and search for its content. Record whether the
  index still returns it.
- M3. Run two processes that load the same table at once with the serialized writer in
  each. Record how many 409s occur and whether the retry bound of the driver is enough.
- M4. With `RUNTIMEDB_ENGINE__SQL_WRITES=true`, time one hundred single-row INSERT
  statements and one hundred single-row upload-plus-load calls into the same table shape.
  Record both per-call costs.
- M5. Load one thousand, ten thousand, and one hundred thousand rows into the `memory`
  table shape. Time the three-stage retrieval query with and without the BM25 and vector
  indexes at each size. Record the size at which an index first beats the scan.
- M6. Against the local container with the three variables above, run every framework
  call the driver needs: create a managed database, declare two tables, load with keyed
  upsert and delete, build a BM25 index and a provider-backed vector index, and query.
  Record which calls work with no API key and no control plane. If all of them do, the
  integration leg can run against the container in CI instead of a throwaway cloud
  database.

## 6. The harness

The library is deterministic. No clock, no network, and no model are needed to run the
offline suite. The harness makes that cheap to prove on every change.

### 6.1 One command

`make verify` runs ruff, ruff format in check mode, strict mypy over the library and the
tests, the offline suite, and the documentation checks in section 6.5, in that order. It
runs in under five seconds. CI calls the same target. There are no tiers for the offline
loop, because a loop under five seconds does not need a way to run less than everything.

### 6.2 The conformance suite

One suite of tests, parametrized over every driver. Each guarantee in section 5 with the
state answered is one test. The suite is the contract. A driver that fails a conformance
test is not a driver.

The `MemoryStore` driver is the oracle for the `HotdataStore` driver. A test that builds
the same records in both and compares `search` results proves the SQL the Hotdata driver
emits does what the in-memory driver computes.

### 6.3 The frozen surfaces

Four surfaces are frozen as set comparisons in tests. Changing any of them is a public
contract change that needs a changelog entry, and the test says so in its failure message.

- The names in `__all__`.
- The method set of the `Store` protocol.
- The field set and the field types of the record, per schema version.
- The filter keys that `list` and `search` accept.

A frozen set compares against a literal set. A test that parametrizes over the thing it
protects turns a deletion into one fewer test instead of a failure.

### 6.4 The guarantees ledger

`docs/guarantees.md` lists every guarantee with the test that proves it and the date it was
measured. A guarantee with no test is written as to measure. The ledger is the file a
consumer reads before relying on a behaviour. A test in the suite reads the ledger and
fails when a named test does not exist.

### 6.5 The documentation checks

Documentation is verified like code, with checks that need no judgement.

- Every relative link in `README.md` and under `docs/` resolves to a file.
- Every command in the skill file in section 7 runs against the in-memory driver and exits
  zero.
- Every guarantee row in the ledger names a test that exists, once the suite exists.

### 6.6 The integration leg

Tests marked `hotdata` run `HotdataStore` against a real database. They need
`HOTMEMORY_TEST_DB` to name a throwaway database that the run created and will delete.
They skip when the variable is unset. They run once per pull request, at the end, because
one run costs minutes. They are the only tests that touch a network.

### 6.7 What is not in the harness

- No coverage gate. A number that reddens for unrelated reasons gets lowered under
  pressure.
- No mutation testing as a gate. Mutation is a one-time exercise run by hand when a
  surface is first frozen, to confirm that the freeze catches a change.
- No model in any test. `capture` takes a callable, and the tests pass a fake that
  returns fixed facts.
- No bespoke report generator. The output of `make verify` is the report.

## 7. The skill file

Agents that use the library through a harness rather than through code get a skill:
`skills/hotmemory/SKILL.md` with a name and a description in front matter, the five memory
operations in prose with one example each, and a `scripts/` folder holding one
command-line entry point per operation. The script is the invocation and the skill says
when to use it. The skill file versions with the library, and section 6.5 runs every
command in it on each `make verify`, so the prose that reaches a model cannot drift from
the API.

## 8. Out of scope for version 1

- A LangGraph `BaseStore` adapter. It lands in `hotdata-langchain` once phase 3 is stable.
  The naming in section 3.2 already matches it, so the adapter is a thin one.
- An MCP server over the memory contract.
- A separate memory service in front of Hotdata. The library runs in the consumer's process
  and the hosted API it needs is Hotdata's own. A service becomes necessary only for a
  client in another language, for extraction that must run centrally, or for scope
  enforcement above the API key. None of the three exists yet.
- Server-side extraction. `capture` takes a callable and that is all.
- A third driver. The local RuntimeDB stack covers offline development with the Hotdata
  driver itself.
- Access control beyond scope filtering.
- A Rust implementation. The Rust client has no framework layer to build on.

## 9. Stop and ask if

- A phase 0 measurement shows that an index does not serve rows loaded after its build,
  and the fix changes the storage contract.
- A guarantee in section 5 cannot be given a test that observes it.
- A frozen surface needs to change before version 1 ships.
- The first consumer needs a field that is not in the record.
- M5 shows the three-stage query is slower than a scan at every size, in which case the
  index plan in section 3.4 is wrong.
