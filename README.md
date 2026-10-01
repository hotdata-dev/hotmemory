# hotmemory

Agent memory as tables on [Hotdata](https://hotdata.dev).

A memory record is a row in a managed table. The row carries the text, an embedding, a
scope, tags, source references, and the time span the fact was true. Because the table is
an ordinary Hotdata table, an agent can join its memory to its own data in one SQL query.
No other memory store offers that join.

Status: design. The contracts are drafted in [docs/internal/brief.md](docs/internal/brief.md). No code
exists yet.

The library has two layers:

- A storage contract. Put, get, list, search, and delete records in a namespace. Records
  are immutable. A new put under the same key creates a new revision.
- A memory contract. Remember facts, recall them inside a context budget, supersede a
  subject, and forget by id or by horizon. Extraction from raw text is optional and takes
  a model callable that the caller supplies.

The first consumer is an incident investigator built on Hotdata. The library is not
specific to it. A plain Python agent, a LangGraph agent, or any process with a Hotdata API
key can use it.
