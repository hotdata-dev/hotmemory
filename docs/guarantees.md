# Guarantees

This file lists each behavior that a consumer can rely on, with its state and its proof.
Read it before you rely on a behavior. The contracts themselves are in
[contracts.md](contracts.md).

## States

Each guarantee has one of two states.

- Answered: a measurement or the design already supports the answer.
- To measure: a measurement must give the answer before the library relies on it.

A guarantee marked [measured] was observed against a real Hotdata workspace. A guarantee
marked [measured, local] was observed against the local stack in [local.md](local.md).

The Test column names the tests that prove the guarantee. A test in
`tests/test_conformance.py` proves it against every driver. A test in
`tests/test_hotdata.py`, `tests/test_hotdata_retry.py`, or `tests/test_oracle.py` proves
a row that only the Hotdata driver can show. If a named test does not exist,
`tests/test_ledger.py` fails. A row that no test proves yet names the phase that will
prove it. Phase 3 proves the rows of the memory contract.

## The ledger

| Question | Answer | State | Test |
|---|---|---|---|
| Does a second `put` under the same key replace the record? | No. It writes revision n+1 and marks revision n as superseded. `get` returns n+1. | answered || `test_second_put_writes_a_new_revision` |
| Can a retried `remember` create duplicates? | No. The key comes from the subject and a hash of the normalized content. A put whose normalized content is equal to the current revision writes nothing, unless it brings a source that the current revision lacks. | answered || phase 3 |
| Does a put of the same content with a new source write anything? | Yes, one new revision. Its `sources` are the current sources followed by the new ones, in order, without repeats. A put of the same content with no new source writes nothing and returns the current id. | answered | `test_same_content_with_a_new_source_writes_a_merged_revision` |
| Is a paraphrase a duplicate? | No. Deduplication is exact on normalized content. `candidates` exists so that a caller can decide. | answered || `test_deduplication_is_exact_on_normalized_content` |
| After a synchronous `put` returns, does `list` see the record? | Yes. A read after a write was never stale in 30 trials. | answered [measured] || `test_synchronous_put_is_visible_to_list` |
| After a synchronous `put` returns, does `search` see the record? | Yes. Without an index, the retrieval query scans the table. With a provider-backed vector index or a BM25 index, the first search after the load returned the new row. | answered [measured], M1 || `test_synchronous_put_is_visible_to_search` |
| After a buffered `put` returns, is the record visible? | No. It is visible after the writer flushes. The writer records the ids that it flushed. | answered || `test_buffered_put_is_visible_after_flush` |
| Two processes write to the same table. What happens? | The engine refuses the second load with 409 `RESOURCE_LOCKED`. The driver retries up to 8 attempts, with a backoff from 0.25 seconds that doubles to at most 4 seconds, and then raises the error. Inside one process, the store sends one load at a time for each table. The integration test runs two stores in one process, which the engine cannot tell apart from two processes. | answered [measured], M3 | `test_two_stores_write_one_database_at_once`, `test_a_locked_load_retries_with_a_doubling_backoff`, `test_a_load_stops_after_the_last_attempt` |
| Two writers in one process put the same key. What happens? | The last writer wins at the row level. Revisions are new rows, so both revisions exist and the later one is current. This holds inside one process only. The next row gives the rule for two processes. | answered | `test_last_writer_wins_on_one_key`, `test_one_store_serializes_writes_on_one_key` |
| Can two processes write to one database? | Not safely. Two processes that put the same key can both read the same current revision. Both then write the same next revision, and the later load replaces the earlier row, so one revision is lost. One process writes to a database. Other processes can read it. This rule stays until the engine has a conditional write. | answered | `test_one_store_serializes_writes_on_one_key` |
| Does `delete` remove retained revisions and their embeddings? | Yes. The content vector is a column of its row, and `HotdataStore` deletes the cue rows of every revision with the record rows. After a keyed delete, neither a provider-backed vector index nor a BM25 index returned the deleted row. | answered [measured], M2 | `test_delete_removes_every_revision`, `test_delete_removes_rows_and_cues` |
| Which filters work in `list` and `search`? | Equality on `kind`, `subject`, `tags`, and `actor`. A range on `valid_from`, `valid_until`, `created_at`, and `expired_at`. A prefix on namespace labels. Any other filter raises an error. | answered || `test_filter_matches_by_equality`, `test_filter_matches_a_time_range`, `test_unknown_filter_key_raises`, `test_prefix_matches_whole_labels` |
| Does a higher score mean more relevant? | The store returns a distance, and a lower distance is closer. The memory contract returns records in order, with no score. An adapter that needs a score converts the distance. | answered || `test_search_returns_distance_closest_first` |
| What does `recall(as_of=T)` return? | The records that are valid at T by the as-of rule in [contracts.md](contracts.md). If `history` is consulted, this includes records superseded after T. If not, it excludes them. | answered || phase 3 |
| Who enforces scope? | The library filters on the allowed scopes of the caller. The platform enforces the database boundary through the API token. A caller that holds the token can go around the library. | answered || phase 3 |
| Can a consumer tell sources, extractions, and hypotheses apart? | Yes, through `kind`, `sources`, and `actor`. An extracted fact carries the name of the extractor in `actor`. | answered || phase 3 |
| Is a record deleted after its `forget_after` time passes? | No. It stops appearing in `list` and `search`. The sweeper deletes it on its next run. | answered [measured] || `test_forget_after_hides_without_deleting`, `test_sweep_deletes_keys_past_forget_after` |
| What does `sweep` delete? | Every revision of each key whose current revision is past its `forget_after`, with the cue vectors of those revisions. A key whose current revision is not past it keeps every revision, also an older revision that is past it. | answered | `test_sweep_deletes_keys_past_forget_after` |
| A record is past its `forget_after`. Does a `put` of the same content write it again? | Yes. Deduplication applies only to a current revision that `list` can return. The put writes a new revision, and `list` and `search` return it. | answered | `test_put_after_forget_after_writes_a_new_revision` |
| Can a caller change a stored record? | No. A record is frozen, and the store copies `payload` on the way in and on the way out. A change to a dict that the caller holds does not reach the store. | answered | `test_stored_record_cannot_change` |
| Where does `HotdataStore` keep an episode? | A `put` of kind `episode` writes to `episode_v1`, and every other kind writes to `memory_v1`. `get`, `history`, `list`, `search`, `delete`, `sweep`, and `list_namespaces` read both tables. The cue vector of a record of either table is a row in `cue_v1`. | answered | `test_episode_goes_to_its_own_table`, `test_key_cannot_cross_the_episode_line` |
| Is `provision` safe to run again? | Yes. If one database has the name, `provision` opens it after a check of its tables, columns, indexes, and the recorded model and vector size. If more than one database has the name, or the one found has another layout, it raises `LayoutError` and creates nothing. Two processes that provision the same new name at the same moment can both create a database, because the platform has no conditional create. | answered | `test_provision_again_opens_the_same_database`, `test_provision_refuses_a_duplicate_name`, `test_provision_refuses_another_layout`, `test_provision_refuses_another_embedding_model` |
| Does a plain vector index serve rows loaded after its build? | Only without a filter. With a `WHERE` filter in the same query, the engine searched the index and returned only the rows that were there at the build. So the vector rankings of `HotdataStore` do not use the index. They filter first and rank every row in scope by a scan, so a search in a narrow scope returns the top k rows of that scope. At 100,000 rows of 64 dimensions, a search in a scope of 1 percent of the rows took a median of 97 ms fused and 26 ms by content vector alone. | answered [measured, local] 2026-10-08 | `test_filtered_search_finds_every_later_write`, `test_search_in_a_narrow_scope_returns_its_top_k` |
| Does `HotdataStore` agree with `MemoryStore`? | Ranked by content vector alone, it returns the same records in the same order, with distances equal within 1e-6. With the fused ranking, it returns the same set when k covers every record. On the test fixture, the largest distance difference was 4.0e-8. | answered [measured, local] 2026-10-08 | `test_vector_ranking_matches_the_oracle`, `test_fused_ranking_returns_the_oracle_set` |
| Can a `put` move a key between `episode` and another kind? | No. A key holds episodes only, or holds no episode at all. A `put` that crosses that line raises an error and writes nothing. A key can change between `fact`, `profile`, and `procedure`. | answered | `test_key_cannot_cross_the_episode_line` |
| What does a `put` with `close_previous` write? | The new revision, and the previous row with `superseded_by`, `valid_until` set to the new `valid_from`, and `expired_at` set to the clock's time. A missing `valid_from`, or one earlier than the previous `valid_from`, raises an error, and the put or the whole flush writes nothing. Without `close_previous`, the previous span stays open. | answered | `test_close_previous_closes_the_current_revision`, `test_close_previous_refuses_a_span_it_cannot_close`, `test_close_previous_on_a_new_key_or_same_content`, `test_put_without_close_previous_leaves_the_span_open` |
| Can a record have empty content? | No. `put` and a buffered `put` refuse content that is empty after normalization, and write nothing. | answered | `test_put_refuses_empty_content` |
| Does a write inside a turn reach a `recall` in the same turn? | No, by contract. A consumer reads what was there before its own capture. | answered || phase 3 |

## Measurements

Phase 0 runs six measurements. M1, M2, and M3 run against a throwaway cloud database that
the run creates and deletes. M4, M5, and M6 were planned for a local RuntimeDB container.
The bare container refuses managed tables, so they also run against throwaway cloud
databases, with `--cloud`. The scripts are `scripts/measure_cloud.py` and
`scripts/measure_local.py`.

Each result below has a number, a date, and the engine that produced it. All six ran on
2026-10-05.

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

Cloud result, 2026-10-05, `api.hotdata.dev`: the engine refused the first INSERT with
`Bad Request`. Production does not turn on SQL writes, so this is the expected result. One
hundred single-row upload-and-append loads took a median of 2,136 ms, a p95 of 2,614 ms, and
a mean of 2,208 ms. This agrees with the 2.1 seconds per call measured earlier.

Local result, 2026-10-05, the local stack in [local.md](local.md) with SQL writes turned
on: one hundred single-row INSERT statements took a median of 17 ms, a p95 of 22 ms, and a
mean of 18 ms. One hundred single-row upload-and-append loads took a median of 24 ms, a p95
of 29 ms, and a mean of 24 ms. Both tables held 101 rows after the calls.

Locally, a load costs about 24 ms, against 2,136 ms in the cloud. So almost all of the
cloud cost is outside the engine. An INSERT is about 30 percent cheaper than a load
locally, but production does not accept it.

### M5. At what size does an index beat a scan?

Load one thousand, ten thousand, and one hundred thousand rows in the shape of the `memory`
table. At each size, time the three-stage retrieval query with and without the BM25 and
vector indexes. Record the size at which an index first beats the scan. Also record
whether the engine accepts a BM25 index beside a plain vector index, and beside a
provider-backed vector index.

Cloud result, 2026-10-05, `api.hotdata.dev`: no index beat the scan by a clear margin
at any size up to one hundred thousand rows. Each query costs about 400 ms at every size,
with or without indexes. That cost is the floor of one query request. The embeddings have
64 dimensions. Each value is the median of 5 runs after one warm-up, with k 10 and a
fusion depth of 100.

| Query | 1,000 rows, without / with | 10,000 rows, without / with | 100,000 rows, without / with |
|---|---|---|---|
| Filter scan, newest first | 379 / 423 ms | 414 / 394 ms | 695 / 407 ms |
| Filtered vector rank | 384 / 397 ms | 402 / 639 ms | 405 / 411 ms |
| Unfiltered vector rank | 392 / 393 ms | 374 / 488 ms | 380 / 404 ms |
| Text match scan (`LIKE`) | 368 / 401 ms | 386 / 616 ms | 375 / 451 ms |
| `bm25_search` | refused / 380 ms | refused / 716 ms | refused / 431 ms |
| Fused three-stage query | refused / 422 ms | refused / 601 ms | refused / 462 ms |

| Index build | 1,000 rows | 10,000 rows | 100,000 rows |
|---|---|---|---|
| BM25 on `content` | 0.7 s | 0.7 s | 0.7 s |
| Plain vector on `embedding`, cosine | 0.7 s | 3.0 s | 65.8 s |
| Sorted on `created_at` | 0.7 s | 0.6 s | 3.1 s |

The loads took 4.0 s, 7.5 s, and 62.9 s. Each load time includes the upload of the parquet
file. At one hundred thousand rows the file holds about 25 MB of embeddings, so the transfer
dominates.

These results lead to four conclusions.

- The only clear gain is the sorted index on `created_at`. It cut the newest-first filter
  scan at one hundred thousand rows from 695 ms to 407 ms.
- The vector index gave no gain at any size. A scan of one hundred thousand vectors of 64
  dimensions is already inside the 400 ms floor.
- `bm25_search` refuses to run without a BM25 index, so the fused query cannot run without
  one. The fused query cost 422 to 462 ms with indexes, close to the floor.
- The 10,000-row column is slower with indexes in three rows. This is one run, and the
  measurement did not repeat it to separate noise from a real cost.

The engine accepted a BM25 index, a plain vector index, and a sorted index on one table. It
refused a provider-backed vector index beside them, with this error:

```text
Embedding-backed vector indexes cannot coexist with other indexes on the same table.
```

Local result, 2026-10-05, the local stack in [local.md](local.md): a local request costs
about 6 to 10 ms, so the cost of the engine shows. The vector index starts to pay between
one thousand and ten thousand rows, and it pays clearly at one hundred thousand rows. The
settings are the same as in the cloud run.

| Query | 1,000 rows, without / with | 10,000 rows, without / with | 100,000 rows, without / with |
|---|---|---|---|
| Filter scan, newest first | 8 / 9 ms | 9 / 12 ms | 13 / 15 ms |
| Filtered vector rank | 8 / 9 ms | 21 / 13 ms | 48 / 15 ms |
| Unfiltered vector rank | 7 / 6 ms | 14 / 9 ms | 47 / 6 ms |
| Text match scan (`LIKE`) | 7 / 9 ms | 9 / 12 ms | 20 / 19 ms |
| `bm25_search` | refused / 6 ms | refused / 8 ms | refused / 8 ms |
| Fused three-stage query | refused / 17 ms | refused / 21 ms | refused / 21 ms |

| Index build | 1,000 rows | 10,000 rows | 100,000 rows |
|---|---|---|---|
| BM25 on `content` | 0.2 s | 0.2 s | 0.2 s |
| Plain vector on `embedding`, cosine | 0.2 s | 1.4 s | 21.9 s |
| Sorted on `created_at` | 0.2 s | 0.2 s | 0.4 s |

The script polls an index build every 0.2 seconds, so 0.2 s means that the build was done
at the first poll. The loads took 0.2 s or less.

At one hundred thousand rows, the fused query with indexes took 21 ms, against 48 ms for
the filtered vector scan alone. Thus the three-stage query is faster than a scan at that
size. Locally, the sorted index gave no gain. The stack has no embedding provider, so the
local run did not test a provider-backed index beside the others.

Across both runs, an index saves engine time from about ten thousand rows. In the cloud,
the cost of one request hides that saving up to at least one hundred thousand rows.

### M6. Does the driver work against a bare container?

Against a local container with no API key and no control plane, run each framework call
that the driver needs: create a managed database, declare two tables, load with keyed
upsert and delete, build a BM25 index and a provider-backed vector index, and query. Record
which calls work. If all of them work, the integration tests can run against the container
in CI, in place of a throwaway cloud database.

Bare container result, 2026-10-05, with the `latest` image pulled on that day (digest
`sha256:302371bb1923`): the first call fails. The container refuses to create a managed
database, with `managed catalogs require ducklake.metadata_pg_url to be configured`.
[local.md](local.md) explains the cause.

Cloud result, 2026-10-05, `api.hotdata.dev`: every call works. The calls create a
database with two keyed tables and load it in replace, upsert, and delete mode. They build
a BM25 index and run `bm25_search`. They build a provider-backed vector index
(`sys_emb_openai`) and run `vector_search`.
The row count after the loads was 3, as expected. This is the reference for the local
result.

Local result, 2026-10-05, the local stack in [local.md](local.md), with Postgres and
RustFS beside the engine: every call works except the provider-backed vector index. It
fails with `Embedding provider 'sys_emb_openai' not found`, because the stack configures no
embedding provider. The row count after the loads was 3. So the integration tests can run
against the local stack in CI, with plain vector indexes in place of provider-backed
ones.
