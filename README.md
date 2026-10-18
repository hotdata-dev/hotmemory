# hotmemory

Agent memory as tables on [Hotdata](https://hotdata.dev).

A memory record is a row in a managed table. The row carries the text, a scope, tags,
source references, and the time span in which the fact was true. The table is an ordinary
Hotdata table, so an agent can join its memory to its own data in one SQL query. None of
the memory systems that we surveyed stores memory as typed columns in the same engine as
the data of the consumer.

Status: version 0.0.0, not published. The storage contract exists in Python with two
drivers. `MemoryStore` runs in process memory. `HotdataStore` keeps records in one Hotdata
managed database. The memory contract, `Memory`, runs over either driver.
[docs/internal/roadmap.md](docs/internal/roadmap.md) lists the phases.

The library has two layers:

- A storage contract. Put, get, list, search, and delete records in a namespace. Records
  are immutable. A new put under the same key creates a new revision.
- A memory contract. Remember facts, recall them inside a context budget, supersede a
  fact, forget by id or by horizon, and load the profile of a subject. Extraction from raw
  text is optional, and it takes an extractor callable that the caller supplies. `load`
  cuts a Markdown document into chunks, stores each chunk as an episode, and extracts
  facts that name their episode as a source. `capture` with an `episode` key stores its
  text as an episode in the same way, or as chunks with `chunk_chars`. `profile` returns facts, profiles, and procedures,
  never episodes. `recall` returns them too, and adds episodes after the facts only when
  the caller asks for them.

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

## Try the memory contract

`Memory` gives an agent the memory operations over any store. `remember` derives each key
from the subject and the content, so a retried call writes nothing new. `recall` returns
the records and one block of text, with one line for each record:

```python
from hotmemory import Fact, Memory, MemoryStore


def embed(texts):
    return [[text.count("disk") + 0.1, text.count("cpu") + 0.1] for text in texts]


memory = Memory(MemoryStore(embedder=embed))
scope = ("team", "alerts")
memory.remember(
    [Fact(kind="fact", subject="disk", content="The disk fills at night.", sources=("chat-1",))],
    scope,
)
records, block = memory.recall("disk", [scope])
print(block)  # - The disk fills at night. [sources: chat-1] [valid: unknown to now]
```

`load` takes a document name, the Markdown text, a scope, and an extractor. The extractor
gets each chunk, with the first heading and the heading path of the chunk at its top:

```python
from hotmemory import Fact, Memory, MemoryStore


def embed(texts):
    return [[text.count("disk") + 0.1, text.count("cpu") + 0.1] for text in texts]


def last_line(text, observed_at, current):
    return [Fact(kind="fact", subject="disk", content=text.splitlines()[-1])]


memory = Memory(MemoryStore(embedder=embed))
text = "# Disk incident\n\n## Root cause\n\nThe disk fills at night.\n"
episodes, facts = memory.load("disk-incident", text, ("team", "alerts"), last_line)
print(episodes)  # ['team/alerts/disk-incident-0001@1']
episode = memory.store.get(("team", "alerts"), "disk-incident-0001")
print(episode.content.splitlines()[0])  # # Disk incident
```

`skills/hotmemory/SKILL.md` gives an agent the same operations as commands.

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

## Benchmark

hotmemory ran LongMemEval-S, a public benchmark of 500 questions over long chat histories,
on 2026-10-10. [docs/benchmarks.md](docs/benchmarks.md) gives every number with its
configuration.

Retrieval needs no model except the embedder. The runner stored each session of a question
as an episode in `HotdataStore` and searched with the question. The 419 scored questions
had these results with the vector ranking. For 98.3% of them, an evidence session was in
the top 5. For 99.3% of them, every evidence session was in the top 10.

The end-to-end score ran on two samples of 54 questions, sample A on 2026-10-10 and
sample B on 2026-10-14. Later rows give their own date. The reader and the judge were
`gpt-4o-2024-08-06`.

| What the reader got | Sample A | Sample B |
|---|---|---|
| Every session of the question, about 114,000 tokens | 39 of 54 (72.2%) | 43 of 54 (79.6%) |
| The 10 sessions that the search ranked first | 45 of 54 (83.3%) | 48 of 54 (88.9%) |
| The block of `recall`, after `capture` with a `gpt-4o-mini` extractor and the `baseline` prompt | 19 of 54 (35.2%) | 27 of 54 (50.0%) |
| The same, on 2026-10-14, with the `detail` prompt and a session source on each fact | 31 of 54 (57.4%) | 32 of 54 (59.3%) |
| The same, on 2026-10-15, with each session stored as an episode, and `recall` with `episodes="search"` and a budget of 64,000 characters, about 12,800 tokens | 45 of 54 (83.3%) | 45 of 54 (83.3%) |

Facts alone score below the full history. With the `baseline` prompt, the extractor did
not store the answer in 19 of the 35 misses on sample A, and in 16 of the 27 misses on
sample B. With the `detail` prompt, these counts are 16 of 23 and 14 of 22. With the
episodes that a search adds after the facts, the memory arm scores above the full history
and equal to the 10 sessions on sample A, with less than half of their tokens. With 54
questions, each score can be about 10 points off. With the `baseline` prompt, the memory
arm differed by 14.8 points between the two samples. In the cloud, a search took 361 ms at
p50 with the vector ranking, and 998 ms with the fused ranking.

## Documents

- [docs/contracts.md](docs/contracts.md): the record, the storage operations, the memory
  operations, and the platform facts behind them.
- [docs/guarantees.md](docs/guarantees.md): each behavior that a consumer can rely on, its
  state, and its proof.
- [docs/local.md](docs/local.md): how to run the library against a local RuntimeDB
  container.
- [docs/benchmarks.md](docs/benchmarks.md): the numbers of hotmemory on LongMemEval-S,
  each with its configuration. [bench/README.md](bench/README.md) tells you how to run it.
- [CONTRIBUTING.md](CONTRIBUTING.md): the one command that checks a change, and the rules
  of the test suite.
- [CHANGELOG.md](CHANGELOG.md): each change to a public surface.
- [docs/internal/](docs/internal/brief.md): the design brief, the survey, the roadmap, the
  plan for the open workstream, the research that chose the benchmarks, and the
  architecture: how each operation runs on the Hotdata platform.
