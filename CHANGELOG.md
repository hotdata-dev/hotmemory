# Changelog

Each change to a public surface of hotmemory has an entry here. The frozen surfaces are the
names in `hotmemory.__all__`, the method set of `Store`, the fields and field types of
`Record` for each schema version, and the filter keys of `list` and `search`. A test guards
each one, and its failure message points to this file.

New entries go under `## [Unreleased]`. A release moves them into a section named
`## [X.Y.Z] - YYYY-MM-DD`, and that section becomes the notes of the GitHub Release.
[RELEASING.md](RELEASING.md) gives the procedure.

## [Unreleased]

The first release. It needs Python 3.11 or later. The core package has no dependencies.

### Added

- `Record`, the frozen dataclass for schema version 1, and `normalize`, the form that
  deduplication compares.
- `Store`, the protocol of the storage contract, with `put`, `get`, `history`, `list`,
  `search`, `delete`, `sweep`, `list_namespaces`, and `writer`. `Writer` is the protocol of
  the buffer that `writer` returns.
  - Records are immutable. A put under an existing key writes a new revision. A put whose
    content duplicates the current revision but brings a new source writes a new revision
    with the merged sources.
  - `put` takes `close_previous`, which closes the validity span of the current revision
    in the same write.
  - A put that moves a key between `episode` and another kind raises `ValueError`.
  - `sweep` deletes every revision of each key whose current revision is past
    `forget_after`, and returns the deleted ids.
- `Filter` and `TimeRange`, the exact filters of `list` and `search`, and `Hit`, one search
  result with its distance. `Filter.kind` takes one kind or a tuple of kinds.
- `MemoryStore` and `MemoryWriter`, the in-process driver. `MemoryStore` takes `records`,
  the revisions that it starts with, and `records()` returns every revision that it holds.
  A writer flush in which one put raises writes nothing.
- `hotmemory.hotdata`, in the `hotdata` extra, with `HotdataStore`, the driver over one
  Hotdata managed database, and `HotdataWriter`. The extra needs `hotdata-framework` 0.14.1
  and `pyarrow` 16.0 or later.
  - `HotdataStore.provision` opens the database with the name, or creates it.
    `HotdataStore.open` opens a database by id. `LayoutError` reports a database whose
    layout differs.
  - A search ranks by content vector by default, as in `MemoryStore`. `ranking="fused"`
    ranks by reciprocal rank fusion of BM25 and the vector rankings.
  - `provision` and `open` take `vector_index`, an experimental option that builds and
    uses the vector indexes, and `sorted_index`, which builds a sorted index on
    `created_at`. Both are off by default.
- `Memory`, the memory contract over one `Store`, with `remember`, `recall`, `candidates`,
  `supersede`, `forget`, `profile`, `capture`, and `load`. `Fact` is the frozen dataclass
  of one structured fact that `remember` takes.
  - `recall` and `profile` read the records of kind `fact`, `profile`, and `procedure`.
    `recall` takes `episodes`, `"cited"` or `"search"`, to add episodes after the facts
    inside the budget.
  - `capture` takes an extractor callable. With `episode`, it also stores the text as an
    episode, and with `chunk_chars`, as chunks of that size.
  - `load` cuts a Markdown document into chunks by headings and fences, stores each chunk
    as an episode, and runs an extractor on each chunk.
- `hotmemory.openai`, in the `openai` extra, with `OpenAIEmbedder`, an `Embedder` over the
  OpenAI embeddings API.
