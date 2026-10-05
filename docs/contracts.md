# Contracts

hotmemory has two contracts. The storage contract is the stable surface that every driver
implements. The memory contract is the surface that an agent calls, and it is built on one
store. This file states both. The behavior that each contract guarantees, and the proof for
each guarantee, are in [guarantees.md](guarantees.md).

Status: design. No code exists yet. This file describes schema version 1 as the library
will ship it.

## The platform under the store

The Hotdata driver writes to managed tables. A managed table is a table that the Hotdata
API owns and loads from files. The facts below shape the contracts. A fact marked
[measured] was observed against a real Hotdata workspace between 2026-08-18 and
2026-09-18.

- A write is a file load. The caller uploads parquet and calls a keyed load in replace,
  append, upsert, update, or delete mode. [measured]
- A write costs about 2.1 seconds per call, and the cost does not change with row count.
  One row costs 1.75 seconds. Ten thousand rows cost 2.5 seconds. Call count is the only
  cost that a caller controls. [measured]
- There is no conditional write. No load mode compares the existing value, and no load
  mode returns the row that it changed. If two writers load one key, the last writer
  wins. [measured]
- A load locks the whole table. A second load at the same time is refused with
  `409 RESOURCE_LOCKED`. Distinct keys give no concurrency. [measured]
- A read after a write was never stale in 30 loads. A read costs 0.3 to 0.6 seconds. The
  first call to a cold engine costs about 8 to 10 seconds. [measured]
- A table layout is permanent. There is no ALTER. An upsert load with extra columns
  succeeds and drops the extra columns without an error. Only a replace load adds
  columns. [measured]
- After you delete a managed table, its name stays reserved forever. [measured]
- The `expires_at` value on a database is a record only. Nothing deletes the database
  when the time passes. [measured]
- A managed database is a hard isolation boundary. One managed database cannot attach
  another. [measured]
- A managed table can have three kinds of index: BM25 over a text column, vector, and
  sorted. A plain vector index covers a vector column that the caller fills. A
  provider-backed vector index takes a text column and embeds it on the server. In plain
  mode, a query whose metric differs from the index metric does a full scan and gives no
  error. SQL cannot see indexes. Only the control plane (the management API)
  can tell whether an index exists. [measured]
- A provider-backed vector index cannot share its table with any other index. The engine
  refuses the second index. A plain vector index and a BM25 index can share a table.
  [measured 2026-08-18]
- RuntimeDB, the Hotdata query engine, runs on a laptop with a Postgres container and an
  S3-compatible storage container. The bare engine image alone refuses managed tables.
  [measured 2026-10-05] [local.md](local.md) tells you how to start the stack.

The contracts take these decisions from the facts:

- Records are immutable. A revision is a new row, and the new row marks the old row as
  superseded. This gives history, supersession, and safe concurrency on a store whose
  write is a keyed row replace.
- Each table has one writer. The driver sends one load at a time and retries on 409.
- A write is synchronous and slow, or buffered and flushed. The API has both.
  [guarantees.md](guarantees.md) gives the visibility rule for each one.
- Expiry is a column and a sweeper. The platform deletes nothing.
- A database is the tenant boundary. The platform enforces it through the workspace
  allow-list of the API token. A namespace is a column filter inside the database, and
  the library enforces it. The library never claims to enforce more than that.
- The table name carries the schema version. A column change makes a new version and
  never edits a table.
- Each row holds one fact. A document is not a memory. It is an episode that facts point
  back to. The section on tables tells you why.

## The storage contract

The storage contract is a Python protocol named `Store`. Every driver implements it, and
the conformance suite runs against every driver.

### The record

A record is the unit that the store holds. Schema version 1 fixes these fields.

| Field | Type | Meaning |
|---|---|---|
| `namespace` | tuple of strings | Where the record lives. The store keeps it as one path string joined with `/`. A match is on whole labels and never on a string prefix. No label contains `.`. |
| `key` | string | The stable identifier of the caller inside the namespace. |
| `revision` | integer | 1 for the first put under a key. Each later put adds 1. |
| `kind` | string | One of `fact`, `profile`, `procedure`, `episode`. |
| `subject` | string | What the record is about, for example an alert key, a person, or a service. Empty if unknown. |
| `content` | string | The text that a model reads. |
| `cues` | list of strings | Questions or phrases that this record answers. Optional. The driver embeds them apart from `content`. |
| `payload` | JSON object | Structured data that the consumer defines. The store never reads it. |
| `tags` | list of strings | Free labels. You can filter on them. |
| `sources` | list of strings | References to the origin of the record: a thread id, a document path, a run id, an episode key. The length of the list is the corroboration count. |
| `actor` | string | Who wrote this revision: a user id, an agent name, or an extractor name. |
| `created_at` | timestamp | When the store wrote this revision. System clock. |
| `observed_at` | timestamp or null | The time of the source. A post-mortem that you load a year later keeps the incident date here. |
| `valid_from` | timestamp or null | When the fact became true. The default is `observed_at`. Null means unknown. |
| `valid_until` | timestamp or null | When the fact stopped being true. World clock. Null means that it is still true. |
| `expired_at` | timestamp or null | When the store learned that the fact stopped being true. System clock. Null on a current record. |
| `superseded_by` | string or null | The id of the revision that replaced this one. Null on the current revision. |
| `forget_after` | timestamp or null | When a sweeper can delete the record. Null means keep. |
| `forget_reason` | string | The reason for `forget_after`. Empty if `forget_after` is null. |
| `id` | string | `namespace/key@revision`. Derived. The load key. |

The public record has no embedding field. If the caller supplies an embedder, the Hotdata
driver adds embedding columns. If the caller uses a provider-backed index, the driver adds
none. The driver configuration selects one of the two.

A record has two clocks. `valid_until` records the change in the world. `expired_at`
records the moment that the store found out. To ask what memory held on a given day, read `created_at` and
`expired_at`. To ask what was true in the world on that day, read `valid_from` and
`valid_until`.

### The operations

| Operation | Arguments | Behavior |
|---|---|---|
| `put` | namespace, key, record fields | Writes a new revision. If the key exists, the new row gets the next revision, and the previous current row gets `superseded_by`. Returns the id. If the normalized content is equal to the content of the current revision, it writes nothing and returns the current id. |
| `get` | namespace, key, optional revision | Returns the current revision, or the named revision. Returns None if the record does not exist. |
| `history` | namespace, key | Returns every revision, oldest first. |
| `list` | namespace prefix, optional filter, optional since, limit | Returns current revisions under the prefix, newest first. It uses no model and no embedding. |
| `search` | query text or none, namespace prefixes, optional filter, k | Returns up to k current revisions in order of relevance, closest first, each with a distance. It takes the same filter as `list`. With no query text, it is `list`. |
| `delete` | namespace, key | Removes every revision of the key. This is a hard delete. |
| `list_namespaces` | optional prefix | Returns the distinct namespaces under the prefix. |
| `writer` | none | A context manager. It buffers every `put` inside it. The buffer flushes on exit, at a row count, or at an interval, and returns the ids that it flushed. |

The filter accepts equality on `kind`, `subject`, `tags`, and `actor`. It accepts a range on
`valid_from`, `valid_until`, `created_at`, and `expired_at`. Any other filter raises an
error.

`list` and `search` never return a deleted revision, a superseded revision, or a record
past `forget_after`. `history` returns superseded revisions. No operation returns a deleted
revision.

Normalized content is the content in lowercase with each run of whitespace changed to one
space. Deduplication compares the normalized form exactly, and does nothing more. A
paraphrase is a new record. The extractor merges paraphrases, with help from `candidates`
in the memory contract.

### The drivers

Version 1 ships two drivers.

- `MemoryStore` runs in the process, in memory. It is a real driver and not a mock. It
  computes relevance with the same cosine distance that the engine uses, and it refuses a
  filter that it does not model. The offline test suite runs against it, and it is the
  reference for the other driver.
- `HotdataStore` uses one managed database, two tables per schema version, keyed loads, a
  serialized writer, and the retrieval query below. With the local RuntimeDB stack, it is also
  the development driver.

### Tables and retrieval

Each schema version has two tables, because facts and raw material have different shapes
and different write patterns.

The `memory` table holds facts, profiles, and procedures. Its rows are small, revisioned,
and searched often, and `profile` renders them. The `episode` table holds raw material: a
thread, a document, or a post-mortem, cut into chunks of a fixed size. It is append-only
and never revisioned. If a fact is not enough, a consumer searches it. The `sources` of a
fact name the episode keys that it came from. Thus a consumer can go from a fact to its
evidence in one join. Tabular data is never copied into either table. It stays in the
tables of the consumer, and a fact points at it.

Each row holds one fact and not one document, for this reason. If a document is one
record, each small revision is a near-duplicate of the whole document. The cosine distance
between two versions is close to zero, and that is the duplicate problem that a memory
layer exists to prevent. Small facts revise, supersede, and render into a profile without
this problem. Markdown is a good format for the `content` of an episode chunk.

Retrieval is one SQL query over the `memory` table, in three stages.

1. Exact filters come first: namespace labels, validity at the as-of time, `kind`,
   `subject`, `tags`, and `forget_after`. These are plain predicates, and they make the
   candidate set smaller before any ranking.
2. The query ranks the remaining rows by BM25 over `content` and by vector distance over
   `content` and over `cues`. It fuses the rankings by reciprocal rank fusion (it adds
   `1 / (60 + rank)` from each ranking). Fusion is ordinary SQL with common table
   expressions and needs no engine function.
3. A sorted index on `created_at` serves recency order and the sweeper.

Stage 2 needs a BM25 index and vector indexes on one table. A provider-backed vector index
cannot share a table with another index, so stage 2 needs plain vector indexes and an
embedder that the caller supplies. With a provider-backed index and no BM25 index, stage 2
ranks by meaning only. Measurement M5 in [guarantees.md](guarantees.md) records which
arrangement the engine accepts and what each one costs.

A cue is the question that a record answers. The extractor or the caller writes it at
capture time. A query that resembles the question matches the cue, even when the
query and the content share no words. Cues are optional, and retrieval on content alone
always works.

Reads are fast because the table is small. A memory table holds thousands of rows, and a
filtered scan of that is fast without an index. Indexes start to matter at about one
hundred thousand rows. Measurement M5 records the size at which an index first beats the
scan.

## The memory contract

The memory contract is what an agent calls. It is a class named `Memory`, built over one
`Store`. It can change more freely than the storage contract while the library is young.

| Operation | Arguments | Behavior |
|---|---|---|
| `remember` | facts, scope, actor | Writes facts that are already structured. Each fact is a record with `kind` set. The key comes from the subject and a hash of the normalized content, so a retried call writes nothing new. |
| `recall` | query, scopes, optional as_of, budget in characters | Searches the allowed scopes, keeps the records that are valid at `as_of`, and returns the top records inside the budget. It returns them as a list and as one rendered block. The block labels each record with its sources and its validity span, and with nothing else. |
| `candidates` | fact, scopes, k | Returns the k nearest current records with their distances. It makes no decision. A consolidator that the caller writes reads this before it calls `remember` or `supersede`. |
| `supersede` | key of the record to close, new fact, optional valid_from | Closes the named record and writes the new fact as the next revision under its key. The `valid_until` of the old record becomes the `valid_from` of the new record, and the `expired_at` of the old record becomes now. If the `valid_from` of the old record is later than that of the new record, the call refuses. The library decides nothing by itself. |
| `forget` | ids, or a horizon | Deletes the named records, or every record whose `forget_after` is before the horizon. |
| `profile` | subject, scopes, budget in characters | Returns the current records for the subject, grouped by `kind`, as one rendered block inside the budget. The block ends with the namespaces and record counts that `recall` can reach, so an agent knows what it can search for. This is the block that an agent always loads. |
| `capture` | text, scope, actor, observed_at, extractor | Calls the extractor of the caller with the text, `observed_at`, and the current records that `recall` returns for the scope. Then it calls `remember` on the result. The extractor is a plain callable. The library ships no model and names no model. |

A record is valid at time T when `valid_from` is null or at most T, and `valid_until` is
null or after T. A null `valid_from` means the start of time.

These three rules apply to every operation:

- A scope is a namespace prefix. The caller supplies the allowed scopes on each call. The
  library filters on them, and does nothing else to enforce access.
- Memory is a record, not a rule. The block from `recall` and from `profile` says what was
  recorded and when. It never says what to do. If a consumer wants instructions, it
  writes them in its own prompt.
- A write is visible to the next `recall`, and never inside the current turn. A consumer
  that captures and recalls in one step reads what was there before the capture.

## Not in version 1

- An adapter for the LangGraph `BaseStore` interface. The operation names above match it,
  so the adapter will be thin.
- An MCP server over the memory contract.
- A memory service in front of Hotdata. The library runs in the process of the consumer
  and calls the Hotdata API. A service becomes necessary only for a client in another
  language, for extraction that must run centrally, or for scope enforcement above the API
  key.
- Extraction on the server. `capture` takes a callable and does nothing more.
- A third driver. The local RuntimeDB stack covers offline development.
- Access control beyond scope filtering.
