# Survey: five memory systems, read against the brief

Date: 2026-10-01. Each system was read for the same seven questions. Sources are shallow
clones beside this repo (`../mem0`, `../graphiti`, `../langmem`, `../letta-code`) and, for
supermemory, its public docs plus the client code in `../company-brain`. Version and commit
per system are in the first column. Every claim below was checked against a file and line. The
per-system notes with those references are not committed here.

The purpose is to settle the contracts in `brief.md`, in this folder, not to match features. Section 4
lists the changes the survey proposes, and section 5 answers whether to fork mem0.

## 1. What each system is

| System | What it is | Version read |
|---|---|---|
| mem0 | Python library. One extraction call per `add`, hash dedup, a vector store with an opaque payload, a SQLite history file. | 2.2.1, commit 94c3fe9, 2026-09-25 |
| Graphiti | Python library under Zep. A temporal knowledge graph: entities and facts as edges with world-time and system-time clocks, on Neo4j or FalkorDB. | commit 3c42764, 2026-09-30 |
| LangMem | Python library over the LangGraph `BaseStore`. Extraction managers that search, decide, and write through the store. | 0.0.30, langgraph 1.2.10 |
| supermemory | Hosted API. Documents in, server-side extraction and consolidation, memories partitioned by container tag. | docs and SDK 4.20 as used by company-brain |
| Letta (letta-code) | TypeScript coding agent. Memory is a git repository of Markdown files that the agent edits, with a small always-loaded root and the rest read on demand. | commit of 2026-09-30 |

Two of the five changed shape recently, and the change is the finding. mem0 2.2.1 removed
the add, update, delete, none decision loop and the graph store. The add path is one
extraction call, an MD5 check against the ten nearest rows, and an insert. The old decision
prompt is still in the file and nothing calls it. Letta removed the MemGPT memory tools and
the archival vector store. Memory is Markdown files under git, and retrieval is grep plus a
hybrid search over the message transcript. Both moved away from model-driven consolidation
over a vector store, and toward simpler writes with the decision left to the caller or to a
later reflection pass.

## 2. The seven questions

### 2.1 Who extracts

| System | Who extracts | What the caller passes |
|---|---|---|
| mem0 | The library, one model call with a fixed prompt and JSON schema. "When in doubt, extract." | Messages, the scope triple, optional metadata. `infer=False` stores raw text. |
| Graphiti | The library, two model calls, entities then facts, each fact with `valid_at` and `invalid_at` from the text. | An episode: name, body, source type, `reference_time`, `group_id`. |
| LangMem | The library through a manager, one model call with the existing memories in the prompt, Pydantic schemas per memory type. | Messages plus the existing records the manager found by search. |
| supermemory | The server, inside a pipeline called dreaming. The caller steers it with a per-container prose prompt. | A document: content, one container tag, a custom id, flat metadata. A direct memory write also exists. |
| Letta | The agent itself, with Edit and git, plus a reflection subagent every 25 steps. | Nothing. Memory is files the agent rewrites. |

The brief's `capture` takes a callable and names no model. That matches Letta and the
`infer=False` path of mem0, and it is the right default for a library. What every extracting
system also passes, and the brief does not, is context: mem0 passes the ten nearest existing
memories and the last ten messages, LangMem passes the search hits as `existing`, Graphiti
passes the episode time and previous episodes.

### 2.2 How a new fact meets an existing one

| System | Mechanism | Who decides |
|---|---|---|
| mem0 | MD5 of the text against the ten nearest rows. A paraphrase is a new row. | Code. No model decision. |
| Graphiti | Exact and MinHash passes on names, cosine search at 0.6 over 15 candidates, then one model call that returns `duplicate_facts` and `contradicted_facts`. Invalidation is pure time arithmetic after that. | Code first, then a model, then code. |
| LangMem | Search top k, one model call that patches, inserts, or removes, concurrent puts. No conflict detection between runs. | The model. |
| supermemory | Hidden in dreaming. The client sees `updates`, `extends`, and `derives` relations, `isLatest`, and `history`. The client's steering prompt says "Hold exactly ONE current answer per subject". | The server's model. Not observable. |
| Letta | The reflection prompt says "fix the stale entry at the source. Do not append the new version alongside the old." | The agent. |

The one rule all five share is that supersession keeps the old record. supermemory keeps it
with `isLatest=false`, Graphiti with `invalid_at` set, mem0 as a history row, Letta in git
history. The brief's revision row with `superseded_by` is the same shape.

### 2.3 The temporal model

| System | Fields | Point-in-time query |
|---|---|---|
| mem0 | `created_at`, `updated_at`, optional `expiration_date`. Time otherwise lives in the text, rewritten as absolute dates. | None. Expired rows are hidden unless asked for. |
| Graphiti | `created_at`, `valid_at`, `invalid_at`, `expired_at`, `reference_time`. Two clocks: when the world changed and when the system learned it. | Date filters on all four fields, OR of AND groups. Nothing excludes expired edges by default. |
| LangMem | `created_at`, `updated_at` on the store item. | None. |
| supermemory | `version`, `isLatest`, `forgetAfter`, `history`. No validity span. Truth time lives in the text as a date suffix. | None. |
| Letta | Git history. Relative dates are made absolute by the reflection pass. | `git log`. |

Only Graphiti has a real temporal model, and it has the one thing the brief lacks: a second
clock. `invalid_at` is when the fact stopped being true, `expired_at` is when the store
learned that. A replay asks what memory held on a given day, and that is a question on
`expired_at` and `created_at`, not on validity.

### 2.4 Scope

| System | Partition | Enforced where |
|---|---|---|
| mem0 | `user_id`, `agent_id`, `run_id` in the payload. One is required. Metadata cannot override them. | The vector store query. The server checks authentication, not ownership of the id. |
| Graphiti | `group_id` on every node and edge. | A property filter on Neo4j. A separate database per group on FalkorDB. |
| LangMem | A namespace tuple. Prefix match is elementwise on the tuple. No `.` in a label. | The store. Tenant isolation is a convention. |
| supermemory | One container tag per document. One tag per search. Each tag is its own vector namespace. | The server. A cross-tag read is one call per tag, merged by the client. |
| Letta | One git repository per agent. Shared memory is a second repository, never in context. | The filesystem. |

Nobody enforces scope above the store. The brief's answer, the database as the platform
boundary and the namespace as a library filter, is the same answer with a stronger lower
half.

### 2.5 Retrieval and rendering

| System | Search | Rendering into a prompt |
|---|---|---|
| mem0 | Semantic over-fetch, optional BM25, entity boost, sigmoid scoring, optional reranker. | None in the library. The proxy joins texts with newlines. |
| Graphiti | Cosine, BM25, graph traversal, reranked by RRF, MMR, cross-encoder, or node distance. Sixteen recipes. | A JSON context string with `valid_at` and `invalid_at`, no budget. |
| LangMem | `BaseStore.search` with a query, a filter, limit, offset. Score is cosine, higher is better. | The search tool returns item JSON. No budget. |
| supermemory | Hybrid, threshold 0.5 default, optional rerank, filters nested to 8 levels. A profile endpoint returns static, dynamic, and bucketed memories. | company-brain ignores the profile endpoint and renders its own ambient profile from list calls, with no cap. |
| Letta | grep over the files, hybrid search with RRF over the transcript. | The root files are compiled into the system prompt, under 65,536 characters total and 20,000 per file. |

Only Letta has a budget, and only Letta has an always-loaded block. supermemory's profile
exists and its most serious client did not use it.

### 2.6 Forgetting

| System | Mechanism |
|---|---|
| mem0 | Hard delete with a history row. No TTL, no decay. |
| Graphiti | Nothing decays. Invalidation never deletes. `remove_episode` is the only delete. |
| LangMem | `delete` is a put with no value. TTL is a store option, refreshed on read. |
| supermemory | Soft delete with `isForgotten`. `forgetAfter` horizons set by the extractor. The client filters on every read. |
| Letta | Retired content moves to an `ARCHIVE.md`. Git keeps everything. |

The brief's column plus sweeper matches supermemory's client-side filter. The hard delete
matches mem0 and Graphiti.

### 2.7 Storage and whether SQL reaches it

| System | Backend | SQL beside the consumer's data |
|---|---|---|
| mem0 | 24 vector store providers. pgvector stores `id, vector, payload JSONB`. History in a separate SQLite file. | Yes on pgvector, through JSONB paths. History cannot be joined. |
| Graphiti | Neo4j, FalkorDB, Kuzu, Neptune. | No. |
| LangMem | InMemoryStore, PostgresStore. | Yes on Postgres, through the store's own tables. |
| supermemory | Hosted. Postgres and Turbopuffer per code comments. | No. Export is a client-side markdown dump. |
| Letta | Git and Markdown. Transcript on the Letta server. | No. |

So the README claim holds with one qualification. Two systems let a consumer reach memory
with SQL when they run on Postgres, but in both the memory fields sit inside a JSON column
and the history sits elsewhere. None stores memory as typed columns with validity spans in
the same engine as the consumer's data.

### 2.8 Benchmarks

The field measures itself on LongMemEval-S and LoCoMo. supermemory publishes a LongMemEval-S
table against Zep and an open suite named MemoryBench under MIT. Graphiti ships a
LongMemEval evaluation with a model judge and no numbers. mem0 points to a separate
benchmarks repository and claims a LoCoMo delta that this clone cannot reproduce. None of
these measures the question the first consumer asks, which is whether a prior incident's cause reaches the
first round of a repeat. The replay case in the brief stays the first proof. A LongMemEval
run is a later proof that the layer is a general memory and not only one consumer's feature.

## 3. The fork question

Rohan asked whether forking mem0 onto a Hotdata backend gets the orchestration with fewer
steps. The code answers no, and a provider is not worth it either.

The vector store is pluggable without a fork. `VectorStoreBase` has eleven abstract methods
and 24 providers. A Hotdata store can enter through the `langchain` provider, which wraps
any LangChain VectorStore, so the existing one in `hotdata-langchain` works today, or by
assigning the attribute after construction. The history store is not pluggable: it is one
SQLite class with no base class, no factory, and no configuration key, and it also holds the
last ten messages the extraction prompt reads. The model and embedder validators hold
literal provider lists, so a plain callable is not accepted anywhere. The license is Apache
2.0.

What a provider buys is one extraction call and an MD5 check, which is the whole of mem0's
orchestration in 2.2.1. What it costs is mem0's contract: the scope triple as the only
partition, a fixed prompt and JSON schema, a required model and embedder at construction,
a history file beside the process, and an opaque payload in which `update` overwrites in
place. None of the brief's temporal or revision model fits in that payload, and that model is
the part that makes a memory table joinable. A fork keeps the same prompts and adds a
3,900-line `main.py` whose write path changed shape between versions. mem0 is a reference
for the extractor contract, and nothing more.

## 4. Changes the survey proposes to the brief

Eight changes, each tied to what was read. They are proposals until Rohan accepts them.

1. Add `expired_at` to the record beside `valid_until`. `valid_until` is when the world
   changed and `expired_at` is when the store learned it. `supersede` sets both. (Graphiti)
2. Add `observed_at` to the record and to `capture`. It is the time of the source the record
   came from, separate from `created_at` and `valid_from`. The extractor receives it and
   `valid_from` defaults to it. A post-mortem loaded next year has `created_at` next year
   and `observed_at` the incident date. (Graphiti, mem0)
3. Give the extractor context. `capture` passes the text, `observed_at`, and the current
   records that `recall` returns for the scope, so the extractor can skip a known fact.
   (mem0, LangMem)
4. Make `supersede` name the record it closes by key, and use Graphiti's closing rule: close
   the old record at the new record's `valid_from`, and refuse when the old `valid_from` is
   later. Write the as-of rule for `recall` down and test it, including the case where
   `valid_from` is null. The library decides nothing by itself. (Graphiti, mem0)
5. State that deduplication is exact on normalized content, and that a paraphrase is a new
   record. Add `candidates(fact, k)` to `Memory`, which returns near records with distances
   so a caller's consolidator can decide. (Graphiti, mem0)
6. Make `profile` an always-loaded block. One rendered block per subject under a character
   cap the consumer sets, each record with its validity span, ending with the namespaces and
   counts that `recall` can reach so the agent knows to search. (Letta, company-brain)
7. Match the LangGraph `BaseStore` where it costs nothing. Rename `namespaces` to
   `list_namespaces`. Let `search` with no query mean `list`. Reject `.` in a namespace
   label. Match a namespace prefix on labels, not on a joined string. The adapter converts
   distance to a higher-is-better score and routes every put through the writer because the
   store's concurrent puts would hit the table lock. (LangMem)
8. Add an `actor` field for who wrote the revision, and derive a corroboration count from
   the length of `sources`. `sources` says where a fact came from and not who recorded it.
   (mem0, supermemory)

Two things the survey confirms as right and not worth changing: the hard delete, and keeping
extraction out of the library. Two things it adds to the guarantees ledger without a code
change: a write is visible to the next `recall` and never inside the current turn, which is
Letta's rule, and `search` must prove its value on a real recall case, because Letta dropped
its vector archive for grep over indexed files and did not miss it.

## 5. What was not read

The Anthropic memory tool and Google's Memory Bank were covered in conversation from
documentation and not from code. Cognee and the A-MEM paper were not read. The trustcall
package that LangMem uses for patches was not in the clone, so the exact patch format is
unconfirmed.
