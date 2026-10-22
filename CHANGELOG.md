# Changelog

Each change to a public surface of hotmemory has an entry here. The frozen surfaces are the
names in `hotmemory.__all__`, the method set of `Store`, the fields and field types of
`Record` for each schema version, and the filter keys of `list` and `search`. A test guards
each one, and its failure message points to this file.

## Unreleased

### Added

- `Record`, the frozen dataclass for schema version 1, and `normalize`, the form that
  deduplication compares.
- `Store`, the protocol of the storage contract, with `put`, `get`, `history`, `list`,
  `search`, `delete`, `list_namespaces`, and `writer`. `Writer` is the protocol of the
  buffer that `writer` returns.
- `Filter` and `TimeRange`, the exact filters of `list` and `search`, and `Hit`, one search
  result with its distance.
- `MemoryStore` and `MemoryWriter`, the in-process driver.
- `hotmemory.hotdata`, in the `hotdata` extra, with `HotdataStore`, the driver over one
  Hotdata managed database, and `HotdataWriter`. `HotdataStore.provision` creates or opens
  the database by name, and `HotdataStore.open` opens it by id. `LayoutError` reports a
  database whose layout differs. `ranking="vector"` ranks a search by content distance
  alone.
- `Memory`, the memory contract over one `Store`, with `remember`, `recall`,
  `candidates`, `supersede`, `forget`, `profile`, and `capture`. `Fact` is the frozen
  dataclass of one structured fact that `remember` takes.
- `MemoryStore` takes `records`, the revisions that it starts with, and `records()`
  returns every revision that it holds.
- `Memory.load`, which cuts a Markdown document into chunks by headings and fences,
  stores each chunk as an episode, and runs an extractor on each chunk. Each fact names
  the episode of its chunk in `sources`.
- The skill file `skills/hotmemory/SKILL.md`, with one script in
  `skills/hotmemory/scripts/` for each memory operation, including `load.py`.
- `hotmemory.openai`, in the `openai` extra, with `OpenAIEmbedder`, an `Embedder` over
  the OpenAI embeddings API.

### Changed

- `Store` gains `sweep`, which deletes every revision of each key whose current
  revision is past `forget_after`, and returns the deleted ids. `MemoryStore` and
  `HotdataStore` implement it.
- A `put` that moves a key between `episode` and another kind raises `ValueError` in
  every driver.
- `Store.put` and `Writer.put` take `close_previous`, which closes the validity span of
  the current revision in the same write. `MemoryStore` and `HotdataStore` implement it.
- A put whose content duplicates the current revision but brings a new source writes a
  new revision with the merged sources. Before, it wrote nothing.
- A `MemoryStore` writer flush in which one put raises writes nothing, as in
  `HotdataStore`.
- The vector rankings of a fused `HotdataStore.search` filter first and rank by a scan, so
  a search in a narrow scope returns the top k rows of that scope.
- `Filter.kind` takes one kind or a tuple of kinds, and matches a record of any kind named.
  The filter key stays `kind`. In `HotdataStore`, a filter without `episode` reads only
  `memory_v1`.
- `Memory.recall` and `Memory.profile` read the records of kind `fact`, `profile`, and
  `procedure` only. They never return an episode, and the counts of `profile` leave
  episodes out.
- `HotdataStore.provision` builds no vector index by default, so each write skips the cost
  of the index. `provision` and `open` take `vector_index`, an experimental option: with
  it, `provision` builds the three vector indexes, and the vector rankings search through
  them. A filtered search through a vector index can miss rows loaded after its build.
- `HotdataStore.provision` builds no sorted index on `created_at` by default, because a
  load into a table with it takes longer as the table grows. `provision` and `open` take
  `sorted_index`: with it, `provision` builds the sorted index in each record table.
  Databases provisioned before this change keep the index and open as before.
- `HotdataStore` writes a table by SQL `INSERT` when its rows in the write are all new and
  number at most 5, as in a `put` of a new fact. The `INSERT` writes only the ids that the
  table does not hold. Other writes stay keyed loads. If the engine refuses the `INSERT`,
  the store writes by load from then on.
- `HotdataStore` reads the current revisions before a write with lists of at most 500
  keys. Before, one condition for each key could overflow the stack of the engine on a
  flush of about 2,000 rows.
- `HotdataStore.provision` reads every page of the database list. Before, it read the
  first 100 databases, and in a larger workspace it could create a second database with
  the name.
- `Memory.capture` takes `episode`, a key. With it, `capture` also stores the text as an
  episode under that key, after the facts, and each fact names the episode in `sources`.
  A capture of the same text under the same key writes nothing and calls no extractor.
- `Memory.recall` takes `episodes`, `"cited"` or `"search"`. With it, the block adds the
  episodes that its facts cite, or the top episodes for the query, after the facts and
  inside the budget. Without it, `recall` returns no episode, as before. Neither value
  returns an episode past its `forget_after`.
- The block of `recall` and `profile` shows a source that is the id of a record in the
  namespace of its line as `<key>@<revision>`. Before, it showed the full id. The stored
  sources do not change.
- `HotdataStore` ranks by content vector alone by default, as `MemoryStore` does. Before,
  the default was `ranking="fused"`, which stays as an option. On LongMemEval-S, the BM25
  arm of the fused ranking lowered the retrieval score.
- `Memory.capture` takes `chunk_chars`, with `episode`. It cuts the text at blank lines
  into chunks and stores chunk n as an episode under `<episode>-<n>`. The extractor still
  reads the whole text, and each fact names every chunk in `sources`.
- The block of `recall` and `profile` shows a run of sources in the namespace of its line,
  with keys `<prefix>-<n>`, n in four digits counting up by 1, and one revision, as
  `<prefix>-<first>…<last>@<revision>`. The
  stored sources do not change.
