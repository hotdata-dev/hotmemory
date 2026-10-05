# Guarantees

This file lists each behavior that a consumer can rely on, with its state and its proof.
Read it before you rely on a behavior. The contracts themselves are in
[contracts.md](contracts.md).

## States

Each guarantee has one of two states.

- Answered: a measurement or the design already supports the answer.
- To measure: a measurement must give the answer before the library relies on it.

A guarantee marked [measured] was observed against a real Hotdata workspace. In phase 1,
each answered guarantee gets one conformance test, and the Test column names it. If a named test
does not exist, a test in the suite fails. Until the suite exists,
the Test column is empty.

## The ledger

| Question | Answer | State | Test |
|---|---|---|---|
| Does a second `put` under the same key replace the record? | No. It writes revision n+1 and marks revision n as superseded. `get` returns n+1. | answered | |
| Can a retried `remember` create duplicates? | No. The key comes from the subject and a hash of the normalized content. A put whose normalized content is equal to the current revision writes nothing. | answered | |
| Is a paraphrase a duplicate? | No. Deduplication is exact on normalized content. `candidates` exists so that a caller can decide. | answered | |
| After a synchronous `put` returns, does `list` see the record? | Yes. A read after a write was never stale in 30 trials. | answered [measured] | |
| After a synchronous `put` returns, does `search` see the record? | Yes. Without an index, the retrieval query scans the table. With a provider-backed vector index or a BM25 index, the first search after the load returned the new row. | answered [measured], M1 | |
| After a buffered `put` returns, is the record visible? | No. It is visible after the writer flushes. `writer` returns the ids that it flushed. | answered | |
| Two processes write to the same table. What happens? | The engine refuses the second load with 409. The driver retries with backoff and stops after a bound. Inside one process, the writer sends one load at a time. | answered [measured], M3 | |
| Two writers put the same key. What happens? | The last writer wins at the row level. Revisions are new rows, so both revisions exist and the later one is current. | answered | |
| Does `delete` remove retained revisions and their embeddings? | Yes. An embedding is a column of its row. After a keyed delete, neither a provider-backed vector index nor a BM25 index returned the deleted row. | answered [measured], M2 | |
| Which filters work in `list` and `search`? | Equality on `kind`, `subject`, `tags`, and `actor`. A range on `valid_from`, `valid_until`, `created_at`, and `expired_at`. A prefix on namespace labels. Any other filter raises an error. | answered | |
| Does a higher score mean more relevant? | The store returns a distance, and a lower distance is closer. The memory contract returns records in order, with no score. An adapter that needs a score converts the distance. | answered | |
| What does `recall(as_of=T)` return? | The records that are valid at T by the as-of rule in [contracts.md](contracts.md). If `history` is consulted, this includes records superseded after T. If not, it excludes them. | answered | |
| Who enforces scope? | The library filters on the allowed scopes of the caller. The platform enforces the database boundary through the API token. A caller that holds the token can go around the library. | answered | |
| Can a consumer tell sources, extractions, and hypotheses apart? | Yes, through `kind`, `sources`, and `actor`. An extracted fact carries the name of the extractor in `actor`. | answered | |
| Is a record deleted after its `forget_after` time passes? | No. It stops appearing in `list` and `search`. The sweeper deletes it on its next run. | answered [measured] | |
| Does a write inside a turn reach a `recall` in the same turn? | No, by contract. A consumer reads what was there before its own capture. | answered | |

## Measurements

Phase 0 runs six measurements. M1, M2, and M3 run against a throwaway cloud database that
the run creates and deletes. M4, M5, and M6 run against a local RuntimeDB container. The
scripts are `scripts/measure_cloud.py` and `scripts/measure_local.py`.

Each result below is a placeholder until the scripts run. A result gets a number, a date,
and the engine that produced it.

### M1. Does an index serve rows loaded after its build?

Build a provider-backed vector index on a table, load ten more rows, and search for one of
the new rows. Record whether the index serves the new row, and after how long.

Result, 2026-10-05, `api.hotdata.dev`, embedding provider `sys_emb_openai`: the index
served the new row on the first search after the load returned. Each index was built over
50 rows, and then 10 rows were loaded in one upsert.

| Index | Build | Load of 10 rows | New row served |
|---|---|---|---|
| Provider-backed vector | 5.4 s | 3.7 s | at the first search, which returned 1.0 s after the load |
| BM25 | 3.0 s | 2.4 s | at the first search, which returned 0.4 s after the load |

The measurement cannot tell whether the index or a scan of the new rows served the row.
For the contract, the result is the same: a synchronous `put` is visible to `search`.

### M2. Does an index forget a deleted row?

Delete a row that an index covers, and search for its content. Record whether the index
still returns the row.

Result, 2026-10-05, `api.hotdata.dev`: no. After a keyed delete load, a scan counted 0
rows for the deleted id. The provider-backed vector index and the BM25 index did not return
the row, at once or after 30 seconds. This is one trial on each index, on a table of 59
rows.

### M3. Is the retry bound of the writer enough?

Run two processes that load the same table at the same time, each with a serialized
writer. Record how many 409 responses occur, and whether the retry bound of the driver is
enough.

Result, 2026-10-05, `api.hotdata.dev`: the bound is enough. Two processes each made 10
single-row upsert loads into one table at the same time. Each process retried on 409 up to
8 attempts, with a backoff that starts at 0.25 seconds and doubles to at most 4 seconds.

| Process | Loads that succeeded | Loads that gave up | 409 responses | Most attempts for one load | Total time |
|---|---|---|---|---|---|
| 1 | 10 | 0 | 3 | 2 | 26.1 s |
| 2 | 10 | 0 | 5 | 3 | 29.8 s |

Under this contention, a load costs about 2.6 to 3.0 seconds, against about 2.1 seconds
alone.

### M4. What does a SQL INSERT cost against a file load?

With `RUNTIMEDB_ENGINE__SQL_WRITES=true`, time one hundred single-row INSERT statements and
one hundred single-row upload-and-load calls into the same table shape. Record the cost per
call of each.

Result: not measured yet.

### M5. At what size does an index beat a scan?

Load one thousand, ten thousand, and one hundred thousand rows in the shape of the `memory`
table. At each size, time the three-stage retrieval query with and without the BM25 and
vector indexes. Record the size at which an index first beats the scan. Also record
whether the engine accepts a BM25 index beside a plain vector index, and beside a
provider-backed vector index.

Result: not measured yet.

### M6. Does the driver work against a bare container?

Against a local container with no API key and no control plane, run each framework call
that the driver needs: create a managed database, declare two tables, load with keyed
upsert and delete, build a BM25 index and a provider-backed vector index, and query. Record
which calls work. If all of them work, the integration tests can run against the container
in CI, in place of a throwaway cloud database.

Result: not measured yet.
