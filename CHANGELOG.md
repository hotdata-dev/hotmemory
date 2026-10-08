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
- `hotmemory.hotdata`, in the `hotdata` extra, with `HotdataStore.provision` and
  `HotdataStore.open`, which create or open one managed database with the tables of schema
  version 1. `LayoutError` reports a database whose layout differs.
- `hotmemory.openai`, in the `openai` extra, with `OpenAIEmbedder`, an `Embedder` over
  the OpenAI embeddings API.

### Changed

- `Store` gains `sweep`, which deletes every revision of each key whose current
  revision is past `forget_after`, and returns the deleted ids. `MemoryStore` and
  `HotdataStore` implement it.
- A `put` that moves a key between `episode` and another kind raises `ValueError` in
  every driver.
