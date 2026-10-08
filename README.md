# hotmemory

Agent memory as tables on [Hotdata](https://hotdata.dev).

A memory record is a row in a managed table. The row carries the text, a scope, tags,
source references, and the time span in which the fact was true. The table is an ordinary
Hotdata table, so an agent can join its memory to its own data in one SQL query. None of
the memory systems that we surveyed stores memory as typed columns in the same engine as
the data of the consumer.

Status: version 0.0.0, not published. The storage contract exists in Python with two
drivers. `MemoryStore` runs in process memory. `HotdataStore` keeps records in one Hotdata
managed database. The memory contract does not exist yet.
[docs/internal/roadmap.md](docs/internal/roadmap.md) lists the phases.

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

## Try the storage contract

`MemoryStore` needs an embedder for a search with query text. An embedder is a callable
that turns a list of texts into a list of vectors. This example uses a toy embedder that
counts two words:

```python
from hotmemory import Filter, MemoryStore


def embed(texts):
    return [[text.count("disk") + 0.1, text.count("cpu") + 0.1] for text in texts]


store = MemoryStore(embedder=embed)
store.put(("team", "alerts"), "disk", kind="fact", content="The disk fills at night.")
store.put(("team", "alerts"), "disk", kind="fact", content="The disk fills at noon.")
store.put(("team", "alerts"), "cpu", kind="fact", content="The cpu spikes after a deploy.")

print(store.get(("team", "alerts"), "disk").id)  # team/alerts/disk@2
print([r.revision for r in store.history(("team", "alerts"), "disk")])  # [1, 2]
hits = store.search("disk", [("team",)], Filter(kind="fact"), k=1)
print(hits[0].record.content)  # The disk fills at noon.
```

## Store memory in Hotdata

`HotdataStore` needs the `hotdata` extra, and an embedder for every write and search. The
`openai` extra ships `OpenAIEmbedder`, which reads `OPENAI_API_KEY`. Any callable that turns
a list of texts into a list of vectors also works. `provision` opens the database with the
name, or creates it. It reads the connection from `HOTDATA_API_KEY`, `HOTDATA_WORKSPACE`,
and `HOTDATA_API_URL`:

```python
from hotmemory.hotdata import HotdataStore
from hotmemory.openai import OpenAIEmbedder

embedder = OpenAIEmbedder()
store = HotdataStore.provision(
    "agent-memory", embedder=embedder, model=embedder.model, dimensions=1536
)
store.put(("team", "alerts"), "disk", kind="fact", content="The disk fills at night.")
print([hit.record.id for hit in store.search("disk", [("team",)])])
```

One process writes to a database. [docs/contracts.md](docs/contracts.md) gives the tables,
the retrieval query, and the rules of the driver. [docs/local.md](docs/local.md) tells you
how to run it against a local engine.

## Documents

- [docs/contracts.md](docs/contracts.md): the record, the storage operations, the memory
  operations, and the platform facts behind them.
- [docs/guarantees.md](docs/guarantees.md): each behavior that a consumer can rely on, its
  state, and its proof.
- [docs/local.md](docs/local.md): how to run the library against a local RuntimeDB
  container.
- [CONTRIBUTING.md](CONTRIBUTING.md): the one command that checks a change, and the rules
  of the test suite.
- [CHANGELOG.md](CHANGELOG.md): each change to a public surface.
- [docs/internal/](docs/internal/brief.md): the design brief, the survey, the roadmap, and
  the plan for the current phase.
