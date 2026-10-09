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
- The skill file `skills/hotmemory/SKILL.md`, with one script in
  `skills/hotmemory/scripts/` for each memory operation.
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
