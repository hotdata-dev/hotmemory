# hotmemory

Agent memory as tables on [Hotdata](https://hotdata.dev).

A memory record is a row in a managed table. The row carries the text, a scope, tags,
source references, and the time span in which the fact was true. The table is an ordinary
Hotdata table, so an agent can join its memory to its own data in one SQL query. None of
the memory systems that we surveyed stores memory as typed columns in the same engine as
the data of the consumer.

Status: design. No code exists yet. The contracts and the guarantees are written, and
phase 0 measured each guarantee that was open.

The library has two layers:

- A storage contract. Put, get, list, search, and delete records in a namespace. Records
  are immutable. A new put under the same key creates a new revision.
- A memory contract. Remember facts, recall them inside a context budget, supersede a
  fact, and forget by id or by horizon. Extraction from raw text is optional, and it takes
  a model callable that the caller supplies.

The library runs in the process of the consumer and calls the Hotdata API with the API key
of the consumer. There is no hotmemory server. The first consumer is an incident
investigator built on Hotdata. The library is not specific to it. A plain Python agent, a
LangGraph agent, or any process with a Hotdata API key can use it.

## Documents

- [docs/contracts.md](docs/contracts.md): the record, the storage operations, the memory
  operations, and the platform facts behind them.
- [docs/guarantees.md](docs/guarantees.md): each behavior that a consumer can rely on, its
  state, and its proof.
- [docs/local.md](docs/local.md): how to run the library against a local RuntimeDB
  container.
- [CONTRIBUTING.md](CONTRIBUTING.md): the one command that checks a change, and the rules
  of the test suite.
- [docs/internal/](docs/internal/brief.md): the design brief, the survey, the roadmap, and
  the plan for the current phase.
