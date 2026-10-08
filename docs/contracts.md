# Contracts

hotmemory has two contracts. The storage contract is the stable surface that every driver
implements. The memory contract is the surface that an agent calls, and it is built on one
store. This file states both. The behavior that each contract guarantees, and the proof for
each guarantee, are in [guarantees.md](guarantees.md).

Status: the storage contract exists in Python, with two drivers, `MemoryStore` and
`HotdataStore`. The memory contract is design, and this file describes it as the library
will ship it. This file describes schema version 1.

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
- The engine refuses a BM25 index or a plain vector index on an empty table. It allows
  one vector index per table. [measured, local, 2026-10-08]
- A filtered search through a plain vector index returns only the rows that were in the
  table when the index was built. The same search with no filter returns the later rows
  too. [measured, local, 2026-10-08]
- `bm25_search` reads its query text as query syntax. Text such as `disk: full` or
  `(really` makes it refuse the query. [measured, local, 2026-10-08]

The contracts take these decisions from the facts:

- Records are immutable. A revision is a new row, and the new row marks the old row as
  superseded. This gives history, supersession, and safe concurrency on a store whose
  write is a keyed row replace.
- One process writes to a database. Inside that process, the driver sends one write at a
  time and retries a load that the engine refuses with 409.
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
| `namespace` | tuple of strings | Where the record lives. The store keeps it as one path string joined with `/`. A match is on whole labels and never on a string prefix. A namespace has at least one label. A label is not empty and contains no `.` and no `/`. |
| `key` | string | The stable identifier of the caller inside the namespace. A key is not empty and contains no `/` and no `@`. |
| `revision` | integer | 1 for the first put under a key. Each later put adds 1. |
| `kind` | string | One of `fact`, `profile`, `procedure`, `episode`. |
| `subject` | string | What the record is about, for example an alert key, a person, or a service. Empty if unknown. |
| `content` | string | The text that a model reads. It is not empty after normalization. |
| `cues` | tuple of strings | Questions or phrases that this record answers. Optional. The driver embeds them apart from `content`. |
| `payload` | JSON object | Structured data that the consumer defines. The store never reads it. |
| `tags` | tuple of strings | Free labels. You can filter on them. |
| `sources` | tuple of strings | References to the origin of the record: a thread id, a document path, a run id, an episode key. The length of the list is the corroboration count. |
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

In Python, the record is a frozen dataclass. The list fields are tuples, so a record
cannot change after the store writes it. The store copies `payload` when it writes a
record and when it returns one, so a change to a dict that a caller holds never reaches the
store. Every timestamp carries a time zone. The record
refuses a value that the table above does not allow.

The public record has no embedding field. The Hotdata driver keeps the vectors in columns
of its own tables, and `HotdataStore` needs an embedder from the caller to fill them. It
has no mode with a provider-backed index, because that index cannot share a table with the
BM25 index.

A record has two clocks. `valid_until` records the change in the world. `expired_at`
records the moment that the store found out. To ask what memory held on a given day, read `created_at` and
`expired_at`. To ask what was true in the world on that day, read `valid_from` and
`valid_until`.

### The operations

| Operation | Arguments | Behavior |
|---|---|---|
| `put` | namespace, key, record fields | Writes a new revision. If the key exists, the new row gets the next revision, and the previous current row gets `superseded_by`. Returns the id. If the normalized content is equal to the content of the current revision, it writes nothing and returns the current id. This rule compares content only. It does not apply when the current revision is past its `forget_after`, so a `put` of the same content brings the fact back as a new revision. A key holds episodes only, or holds no episode at all. A `put` that moves a key between `episode` and another kind raises an error. |
| `get` | namespace, key, optional revision | Returns the current revision, or the named revision. Returns None if the record does not exist. |
| `history` | namespace, key | Returns every revision, oldest first. |
| `list` | namespace prefix, optional filter, optional since, limit | Returns current revisions under the prefix, newest first, with ties in `created_at` ordered by id. `since` keeps the revisions whose `created_at` is at or after it. It uses no model and no embedding. |
| `search` | query text or none, namespace prefixes, optional filter, k | Returns up to k current revisions in order of relevance, each with the cosine distance between the query and `content`. It takes the same filter as `list`. With no query text, it is `list`, and each distance is None. In `MemoryStore`, relevance is that distance, so the hits come closest first. The section on retrieval gives the order of `HotdataStore`. |
| `delete` | namespace, key | Removes every revision of the key. This is a hard delete. |
| `list_namespaces` | optional prefix | Returns the distinct namespaces under the prefix that hold a record, sorted. |
| `writer` | optional row count, optional interval | A context manager. It buffers every `put` inside it. The buffer flushes when the block exits, when it reaches the row count, and on the first `put` after the interval passes. The writer records the ids that it flushed. If the block raises an error, the writer drops the buffer. |
| `sweep` | none | Deletes every revision of each key whose current revision is past its `forget_after` at the time of the clock. Returns the deleted ids, sorted. A key whose current revision is not past its `forget_after` keeps all of its revisions. |

The filter accepts equality on `kind`, `subject`, `tags`, and `actor`. It accepts a range on
`valid_from`, `valid_until`, `created_at`, and `expired_at`. Any other filter raises an
error. In Python, the filter is a frozen dataclass with one optional field for each key, so
an unknown key cannot be written, and a value of the wrong type raises an error.

- A `tags` filter matches a record that holds every tag that the filter names.
- A range includes its start and excludes its end. A side that is not given is open.
- A null timestamp on a record never matches a range. This is the SQL rule for null.

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
  reference for the other driver. It takes two optional arguments. The embedder is a
  callable that turns a list of texts into a list of vectors. Without it, a `search` with
  query text raises an error. The clock is a callable that returns the current time, and
  the default reads the system clock. `MemoryStore` ranks by the cosine distance between
  the query and `content` only. It does not rank by BM25 or by `cues`.
- `HotdataStore` uses one managed database, keyed loads, a lock for its writes, and the
  retrieval query below. It needs the `hotdata` extra. With the local RuntimeDB stack, it
  is also the development driver. The section on `HotdataStore` below gives the details.

### Tables and retrieval

Each schema version has two record tables, because facts and raw material have different
shapes and different write patterns.

The `memory` table holds facts, profiles, and procedures. Its rows are small, revisioned,
and searched often, and `profile` renders them. The `episode` table holds raw material: a
thread, a document, or a post-mortem, cut into chunks of a fixed size. A consumer appends
to it and does not revise it, but the store does not enforce this. If a fact is not
enough, a consumer searches it. The `sources` of a fact name the episode keys that it came
from. Thus a consumer can go from a fact to its evidence in one join. Tabular data is
never copied into either table. It stays in the tables of the consumer, and a fact points
at it.

Each row holds one fact and not one document, for this reason. If a document is one
record, each small revision is a near-duplicate of the whole document. The cosine distance
between two versions is close to zero, and that is the duplicate problem that a memory
layer exists to prevent. Small facts revise, supersede, and render into a profile without
this problem. Markdown is a good format for the `content` of an episode chunk.

A cue is the question that a record answers. The extractor or the caller writes it at
capture time. A query that resembles the question matches the cue, even when the
query and the content share no words. Cues are optional, and retrieval on content alone
always works.

### HotdataStore

`HotdataStore.provision(name, embedder=..., model=..., dimensions=...)` opens the database
with that name, or creates it. It is safe to run again.

- If exactly one database has the name, `provision` checks its tables, columns, indexes,
  and the model name and vector size that the database records. If they match, it opens
  the database. If not, it raises `LayoutError`.
- If more than one database has the name, it raises `LayoutError` and creates nothing.
- If no database has the name, it creates one, declares the tables, and builds the indexes.
- Two processes that provision the same new name at the same moment can both create a
  database, because the platform has no conditional create. The driver does not prevent
  this.

`HotdataStore.open(database_id, ...)` opens a database by its id, after the same checks.
`client` is a `HotdataClient` from `hotdata-framework`, and defaults to
`HotdataClient.from_env()`.

The database holds four tables for schema version 1.

| Table | Rows |
|---|---|
| `memory_v1` | The records of kind `fact`, `profile`, and `procedure`. It has one column for each field of the record, and `content_embedding`, the vector of `content`. |
| `episode_v1` | The records of kind `episode`, with the same columns. |
| `cue_v1` | One row for each record that has cues: the record id and `cues_embedding`, the vector of the cues joined with newlines. |
| `meta_v1` | One row: the schema version, the embedding model name, and the vector size. |

`namespace` is stored as the labels joined with `/`, `payload` as JSON text, and each
timestamp in UTC to the microsecond. The embedding model and the vector size are fixed for
each database. If an embedder returns a vector of another size, the write or search raises
`ValueError`, and a write sends nothing.

`provision` builds three indexes in each record table: BM25 on `content`, plain cosine
vector on `content_embedding`, and sorted on `created_at`. In `cue_v1`, it
builds a plain cosine vector index on `cues_embedding`. The engine refuses an index on an
empty table. So `provision` loads one seed row into each table, builds the indexes, and
deletes the seed rows.

A `put` reads the current revision, then writes the new row and the superseded row in one
load. If the record has cues, a second load writes its cue row into `cue_v1` at the same
time. The two loads are not atomic. If the cue load fails, the record has no cue row, and
the cue ranking misses it until a retry. If the record load fails, the cue row is left,
and the search drops it. A writer sends one load per table at each flush. `delete` and
`sweep` delete the rows of every revision from the record table and from `cue_v1`.

A store holds a lock across the read and the load of each write, so one store writes in
order. One process writes to a database. Two processes that put the same key can both read
the same current revision and both write the next one. Then the later load replaces the
earlier row. This rule stays until the engine has a conditional write.

If the engine refuses a load with `409 RESOURCE_LOCKED`, the driver tries it again, up to
8 attempts in all. The wait starts at 0.25 seconds and doubles to at most 4 seconds. After
the last attempt, the driver raises the error.

A `search` with query text runs one SQL query over both record tables and `cue_v1`. If
the filter sets `kind`, the query reads only the record table of that kind. With
`ranking="fused"`, the default, the query has three parts.

1. Three rankings: BM25 over `content` in each record table, the cosine distance of
   `content_embedding`, and the cosine distance of `cues_embedding`. BM25 reads only the
   words of the query, each as a quoted term, so query syntax in the text cannot break the
   query. A query with no words gives BM25 no terms, and the two vector rankings still rank.
2. Each ranking keeps its top rows, to a depth of 100 rows, or 10 rows for each requested
   hit if that is more. The exact filters are the namespace labels, `superseded_by`,
   `forget_after`, and the `Filter`. The two vector rankings apply the filters first and
   then rank every row that is left by a scan, so a narrow scope keeps its recall. They do
   not use the vector index, because a filtered search through the index misses rows
   loaded after its build. BM25 fetches its top rows from the whole table and then
   applies the filters, so a narrow filter can leave BM25 with fewer rows than k.
3. Reciprocal rank fusion adds `1 / (60 + rank)` from each ranking, and the query returns
   the k rows with the highest sum. Ties go to the smaller content distance, then to the
   newer row.

The fused order is not the order of the distance, so a later hit can have a smaller
distance than an earlier one. With `ranking="vector"`, the query ranks by the content
distance alone, after the filters, by a scan. That order is the order of `MemoryStore`.

Reads are fast because the table is small. A memory table holds thousands of rows, and a
filtered scan of that is fast without an index. Measurement M5 found that the vector
index saves engine time from about ten thousand rows. In the cloud, one request costs about
400 ms at every size. That cost hides the saving up to at least one hundred thousand rows.
[measured 2026-10-05] Whether the sorted index on `created_at` serves `list` and `sweep`
was not observed.

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
