# mem0 survey: how it extracts, stores, and recalls memories

Survey date: 2026-10-14. mem0 commit `d26ba5e` (2026-10-12), read from the public repository
`mem0ai/mem0`. hotmemory is at branch `phase-6-accuracy-loop` (`5f2abb9`).
RuntimeDB was read at `origin/main` `1bec988` (2026-10-08). The vector extension was read at the
pinned rev `a4d4a650`.

Every mem0 statement here comes from reading source. Nothing in mem0 was run. The mem0 benchmark
scores are mem0's own claims and are not observed. hotmemory numbers come from
`hotmemory/docs/benchmarks.md` and `docs/guarantees.md`, which record observed runs.

## 1. Summary

The mem0 version 3 design is simpler than its marketing suggests.

- Writing a memory is one LLM call with a long, recall-biased extraction prompt. The pipeline only
  adds memories. It never updates or deletes them. The old ADD/UPDATE/DELETE/NONE decision step is
  gone from `add`.
- Deduplication in code is an exact MD5 match of the memory text against 10 retrieved neighbors.
  The LLM catches every near-duplicate through the prompt, or nothing catches it.
- The "graph" is a second vector collection of entities. spaCy (a rule-based NLP library) extracts
  these entities, not an LLM. The Neo4j graph store was removed from the open-source code.
- Recall makes no LLM call. Dense vector search supplies all candidates. A keyword score and an
  entity boost only reorder those candidates. An optional reranker reorders the final top k.
- mem0 claims that the add-only redesign raised LongMemEval from 67.8 to 93.4 and LoCoMo from 71.4
  to 91.6 (`docs/changelog/highlights.mdx:220-236`). Not observed. These are the Platform numbers
  and the Platform has features that the open-source code lacks (section 6).

The accuracy comes mostly from the extraction prompt, not from the storage or the retrieval
machinery. This matches the hotmemory diagnosis: 19 of the 35 memory misses on sample A were facts
that the extractor never wrote (`hotmemory/docs/benchmarks.md:180-191`).

## 2. How mem0 creates a memory

Entry point: `Memory.add` in `mem0/memory/main.py:760-879`. With `infer=True`, the code runs the
"V3 phased batch pipeline" (`main.py:918-1220`).

1. Scope and metadata. The caller gives at least one of `user_id`, `agent_id`, `run_id`. Identity
   keys are stripped from the caller's metadata, so metadata cannot move a memory into another
   scope (`main.py:314-404`).
2. Context, part one. mem0 loads the last 10 raw messages for this exact id combination from
   SQLite (`storage.py:265-302`). Each message is cut to 300 characters in the prompt. A new
   `run_id` gives a new scope with no earlier messages.
3. Context, part two. mem0 embeds the whole new batch as one query and fetches the 10 nearest
   existing memories in the scope, with no similarity threshold (`main.py:926-940`). Their UUIDs
   are replaced by "0" to "9" in the prompt, so that the model cannot invent an id.
4. One LLM call. The system prompt is `ADDITIVE_EXTRACTION_PROMPT`. The user prompt holds the
   existing memories, the last messages, the new messages, the observation date, the current date,
   and optional custom instructions. The call asks for `response_format={"type": "json_object"}`
   (`main.py:943-971`).
5. Parse. The code strips code fences and `<think>` blocks, then reads JSON. If that fails, it
   takes the text from the first `{` to the last `}`. Only `text` and `attributed_to` are read.
6. Embed. One `embed_batch` call for all extracted memories.
7. Deduplicate. The code drops a memory whose MD5 equals the MD5 of one of the 10 retrieved
   memories, or of another memory in the same batch (`main.py:1009-1041`).
8. Persist. One batch insert into the vector store. The payload holds `data`, `text_lemmatized`
   (spaCy lemmas for keyword search), `hash`, `created_at`, `updated_at`, `attributed_to`, the
   scope ids, and the caller metadata. A history row with event `ADD` goes to SQLite.
9. Link entities. spaCy finds entities in the new memories (section 4 lists the rules). Each
   entity matches an existing entity row by exact normalized text or by cosine similarity of 0.95
   or more. On a match, the memory id joins the entity's `linked_memory_ids`. Otherwise mem0
   inserts a new entity row (`main.py:1100-1204`).
10. Save the new raw messages to SQLite and keep only the 10 newest for the scope.

Cost per `add`: 1 LLM call, about 3 embedding round trips (batch query, memories, entities), and
about 5 vector store calls. One of these calls lists up to 10,000 entities of the scope on every
add (`main.py:586-603`). That step gets slower as the scope grows.

With `infer=False`, each message becomes one memory with no LLM call and no deduplication.

## 3. The extraction prompt

`ADDITIVE_EXTRACTION_PROMPT` is at `mem0/configs/prompts.py:468-944`. It is about 480 lines: a
role, the meaning of each input, guidelines, 11 worked examples, a checklist, and the output
format. The rules that matter for accuracy:

- Recall bias. "When in doubt, extract. A slightly redundant memory is far less costly than a
  missing one" (`:578`). For 10 or more messages, it expects 5 to 15 memories. If there are fewer
  than 3, the model reads again (`:912`). It warns against "first topic dominance" (`:915`).
- Both speakers. "You extract from BOTH user and assistant messages" (`:474`). Assistant content
  is framed in the user's context, for example "User was recommended X" (`:488`). The output has
  an `attributed_to` field.
- Incidental facts. The model extracts facts mentioned in passing, not only the request
  (`:584-593`).
- Self-contained memories. No pronouns. Each memory is 15 to 80 words and holds the fact plus its
  context ("contextually rich, not atomic", `:607-632`). A proper noun, title, date, or detail is
  never dropped to meet the word count.
- Temporal grounding. The observation date is the only anchor for relative dates. "Last week"
  becomes a dated range. The current date must not resolve a relative date (`:524-540`).
- Precision. "416 pages" stays "416 pages". "Ferrari 488 GTB" stays, not "sports car"
  (`:637-666`).
- Changes of state. The model writes a transition memory, for example "User switched from almond
  milk to oat milk", instead of an update (`:612-620`).
- Exclusions. No greetings, no filler, no echo of what the user just said, no fabricated
  demographics. If the content of a document is available, the model extracts that content, not
  "User shared a document" (`:490-493`, `:679-689`).

The prompt says that "users search by name: a memory without the name is unfindable." The
specificity rules are a retrieval rule: exact names feed the keyword channel and the entity boost.

## 4. How mem0 recalls a memory

Entry point: `Memory.search` at `main.py:1393`, then `_search_vector_store` at `main.py:1642-1745`.
Defaults: `top_k=20`, `threshold=0.1`, `rerank=False`.

1. Preprocess the query. spaCy lemmatizes it the same way as the stored `text_lemmatized`. spaCy
   also extracts entities from it (`mem0/utils/entity_extraction.py:751`). The entity sources
   are named entities (person, organization, place, product, event), dotted identifiers such as
   `foo.bar`, capitalized spans, quoted text, and specific noun phrases. Dates and numbers are
   rejected.
2. Embed the query (1 call).
3. Semantic channel. Fetch `max(4 * top_k, 60)` nearest memories with the scope filters pushed
   into the store.
4. Keyword channel. Call the store's `keyword_search` over `text_lemmatized` with the same
   filters. Qdrant uses real BM25 with server-side IDF. pgvector uses Postgres full-text search
   with `ts_rank_cd` and `plainto_tsquery`, which needs every query term to match.
5. Normalize the keyword score with a sigmoid whose midpoint depends on the query length
   (`mem0/utils/scoring.py:31-54`). For 3 terms or fewer, the midpoint is 5.0.
6. Entity boost. Embed up to 8 query entities (1 batch call) and search the entity collection
   with `top_k=500` for each. A match of similarity 0.5 or more boosts every linked memory by
   `similarity * 0.5 * 1 / (1 + 0.001 * (n - 1)^2)`, where n is the number of memories linked to
   the entity. An entity linked to many memories counts for less (`main.py:1747-1823`).
7. Fuse. A memory whose raw cosine score is under the threshold is dropped first. Then:

   ```
   divisor = 1 + (any keyword hit ? 1 : 0) + (any entity hit ? 0.5 : 0)
   score   = min((semantic + keyword + entity_boost) / divisor, 1)
   ```

   This is a linear sum, not reciprocal rank fusion (`scoring.py:60-139`).
8. Optional rerank of the final top k: Cohere, a local cross-encoder, a Hugging Face model,
   ZeroEntropy, or an LLM that makes one call per memory.

Cost per search: 0 LLM calls, 1 or 2 embedding calls, 2 store queries plus up to 8 entity
queries.

Weaknesses in this design, read from source:

- Only the semantic channel supplies candidates. A memory that matches by keyword or by entity,
  but not by vector, is never returned.
- The divisor depends on the other results. If any other memory has a keyword hit, a memory with no
  keyword hit drops to about half its cosine score. Scores are not comparable across queries.
- On pgvector, `ts_rank_cd` values are usually below 1, but the sigmoid midpoint is 5. A keyword
  hit then adds about 0.03 and still doubles the divisor. This is inferred, not observed.
- The threshold applies to the raw cosine score, so a strong keyword match cannot save a memory.
- The reranker sees only the final top k, so it cannot recover anything below the cut.

## 5. The agent plugin core

The plugins for Claude Code, Cursor, Codex, and others share the Python core in
`integrations/agent-plugin-core/python/`. It does no fact extraction itself. It sends the
conversation to the hosted mem0 API with `infer: True` and lets the server extract.

Capture:

- Hooks record events into a local SQLite file `evidence.sqlite3` (WAL mode). Events are session
  start, each user prompt, each tool use (a summary, not the full output), each assistant stop,
  and subagent start and stop (`memory_core.py:555-688`).
- For Claude Code, the adapter reads the transcript incrementally from a stored byte offset. It
  keeps human prompts, assistant text, subagent assignments and replies, and approved plans.
- `redact()` removes secrets before storage and again before upload. The patterns cover bearer
  tokens, API keys, AWS keys, GitHub and Slack tokens, PEM keys, and JSON fields such as
  `"password"` (`memory_core.py:140-161`).

Flush:

- A packet of events is flushed after 5 completed exchanges, 10 messages, or 40,000 characters.
  It is also flushed 5 minutes after the last turn, and at compaction and session end. A packet
  always ends on an assistant stop, so an exchange is never split.
- A detached worker process does the upload. Packet ids are deterministic, events are claimed in
  a transaction, and a packet is retried up to 5 times.
- The upload is the conversation text, plus the changed file paths. Commands are added only if
  one failed. Files read, searches, and passing tests are never sent, because they are transient
  detail.
- Each upload carries two instruction sets: one for project facts under `agent_id` and one for
  personal preferences under `user_id`. It also carries five categories (project knowledge,
  decisions and constraints, workflows, problems and fixes, results).

Recall:

- Automatic recall runs once per session, on the first prompt only, with `top_k` 5, no threshold,
  and a budget of 4,000 characters. The memories go into the agent context as a numbered list
  under a one-line heading.
- After the first prompt, the agent must call the MCP tool `search_memories` itself. The tool
  takes a query, `top_k`, a category, and a scope (`repo`, `dir`, or `mine`).
- A memory already shown in a session is not shown again in that session.

## 6. Gaps in the open-source mem0

These are differences between what the prompt or the docs promise and what the open-source code
does. Each one was read from source.

- Memory links are dropped. The prompt asks for `linked_memory_ids`, but the code shows integer
  ids, builds a `uuid_mapping` at `main.py:937-939`, and never reads it. The LLM links are lost.
  I searched the code on 2026-10-14 and found no other use.
- The observation date is always today. `_resolve_dates` sets it to the current date
  (`prompts.py:1007-1013`), and `add(timestamp=...)` raises "platform-only". A replay of old
  conversations resolves "last week" against the import date. This defeats the temporal rules of
  the prompt on any benchmark replay.
- The summary and the recently extracted memories are always empty in open source, but the
  prompt still describes them.
- The `name` field of a message never reaches the LLM.
- The TypeScript and Python SDKs write different payload field names (`textLemmatized` against
  `text_lemmatized`), so neither SDK can read a store that the other wrote. In about 12
  TypeScript stores, `keywordSearch` returns null with no warning.

The conclusion: the published scores come from the hosted Platform. The open-source code is a
reduced copy of it. Do not use open-source mem0 as a measured baseline without a run of our own.

## 7. hotmemory and mem0 side by side

| Concern | mem0 (open source) | hotmemory today |
|---|---|---|
| Extraction | 1 LLM call in the library, long prompt | Caller-supplied callable. The benchmark uses one gpt-4o-mini call with a short prompt (`bench/prompts.py:69-90`) |
| Context to extractor | 10 nearest memories, last 10 messages | Up to 10 recalled records, 2,000 characters (`contract.py:395`) |
| Update and delete | None. Transition memories only | None automatic. `supersede` is explicit. Phase 10 plans `Fact.replaces` |
| Deduplication | Exact MD5 against 10 neighbors | Exact normalized text against the whole namespace, with merged sources |
| Time | One `created_at`. Observation date is today | Bitemporal: `created_at`, `expired_at`, `valid_from`, `valid_until`, `observed_at` |
| Candidates | Vector only | BM25, content vector, and cue vector, each a candidate source |
| Fusion | Linear sum with a per-query divisor | Reciprocal rank fusion, `1/(60 + rank)` |
| Keyword | Store-dependent. Real BM25 only on some stores | Tantivy BM25 in the engine |
| Entities | spaCy entity collection, used as a boost | None |
| Rerank | Optional, final top k only | None |
| Provenance | `attributed_to`, history table | `sources`, `actor`, revision history |

hotmemory already has a stronger retrieval and storage design than open-source mem0. The gap is
the extractor and what it gives the reader.

## 8. What mem0 does that hotmemory does not, and what helps us

These are ranked by the measured miss causes on sample A.

1. Extraction prompt (19 of 35 misses). Our prompt asks for "facts that a personal assistant
   must remember about the user", as "one short sentence". mem0 asks for both speakers, says
   "when in doubt, extract", sets a minimum count, and wants 15 to 80 words of context per
   memory. Phase 7 is the place to test a mem0-style variant. Candidate changes:
   - Extract from the assistant turns with the same weight, framed as "the user was told X".
   - Add "when in doubt, extract" and a minimum count for long sessions.
   - Replace "one short sentence" with a self-contained memory that keeps its context.
   - Convert relative dates to absolute dates with the session date. Our prompt passes the date
     but does not say to convert. We already pass the true session date, which mem0 open source
     does not.
   - Add explicit precision rules: no rounding, keep full product names and titles.
2. Assistant content and episodes (part of the 14 "some present" misses). mem0 does not store
   raw episodes for recall. Phase 8 (episodes behind facts) goes further than mem0 here.
3. Entity boost. The spaCy entity layer is cheap, uses no LLM, and helps name-heavy questions.
   On our stack, it is an `entity_v1` table with the entity text, the entity type, and the memory
   id. BM25 or a vector over the entity text gives a fourth RRF ranking. Test it in phase 9, after phases 7 and 8.
4. Reranker. mem0 makes it optional and applies it after the cut. If we add one, apply it to the
   fused top 50, not the final top k.
5. Agent plugin design, for the first consumer. Three parts are worth copying: flush on exchange
   boundaries, keep transient tool output out of extraction, and run two lanes (project facts and
   personal preferences) with separate instructions.

Do not copy these parts of mem0:

- Linear fusion with a per-query divisor. Our RRF is the better choice.
- Vector-only candidates. We already take candidates from every channel.
- The 10-neighbor MD5 check as the only deduplication. Our whole-namespace check is stronger.

## 9. What the HotData platform can support

Source: RuntimeDB `origin/main` and the hotmemory measurement docs. "Inferred" means read from
source and not measured.

| mem0 mechanism | Platform support | Notes |
|---|---|---|
| Vector search | HNSW (USearch) and IVF indexes. `cosine_distance` and two other distance functions | One vector index per table. hotmemory bypasses the index because a filtered search missed new rows. The engine now maintains the index on each load and has adaptive pre-filtering (inferred). Run the measurement again before phase 9 |
| Keyword search | `bm25_search` table function on Tantivy 0.22 | Real BM25, better than pgvector `ts_rank_cd`. The provider claims WHERE filters for a top k inside the filter (#791, inferred). That contradicts `contracts.md:272-273`. Check it, because it fixes "a narrow scope starves BM25" |
| Lemmatization | None in the engine. Tantivy's default tokenizer lowercases but does not stem (inferred) | Lemmatize in Python before the write and before the query, like mem0 does, into a `content_lemmatized` column |
| Entity collection | Plain tables plus BM25 or a vector index | A separate table, because of the one-vector-index rule |
| Embedding | Client side today. The engine can embed with a provider index and `vector_search('t', 'col', 'text', k)` | A provider index cannot share a table with another index |
| Metadata filters | `json_get`, `->>`, and other JSON functions are registered | hotmemory's `Filter` exposes none of this yet |
| Writes | About 2.1 s per load in the cloud, 24 ms locally. Whole-table lock. No compare-and-set | mem0's own add is 1 LLM call plus embeddings, so it already takes seconds. A buffered async writer hides the load cost |
| Reads | About 1 s fused p50 in the cloud, 130 ms locally (`benchmarks.md:103-124`) | The cloud floor is about 400 ms per request. If an extra RRF ranking stays in the same SQL query, it costs engine time, not a round trip |
| Custom ranking functions | Rust UDFs only, with a RuntimeDB rebuild and redeploy | Keep fusion and boosts in SQL or Python |

The platform supports every retrieval mechanism that mem0 uses. In two places it does better than
the stores that mem0 uses: real BM25, and filtered top k. The platform cost is write latency, and
the hotmemory write path already hides it with buffering. Nothing in the mem0 design needs new
engine work. An entity table, a lemmatized column, and a reranker are all library-side changes.

## 10. Model providers

No library in this survey supports every model provider by itself. Each keeps a fixed list of
provider classes, or passes the work to a meta-library, or lets the caller pass in a client. Most
providers expose an OpenAI-compatible API, so one OpenAI client with a different `base_url`
reaches them.

mem0 Python has 18 LLM providers and 11 embedders in a name registry (`mem0/utils/factory.py:42-164`).
`register_provider` adds a class at runtime. The providers fall into three groups:

- Hand-written adapters over a native SDK: openai, azure_openai, anthropic, gemini, groq,
  together, ollama, sarvam. An adapter is 90 to 160 lines. The real logic is 20 to 40 lines:
  build the parameters, call the SDK, and convert tool calls to one common shape.
- OpenAI-compatible providers: deepseek, xai, minimax, lmstudio, vllm. Each file is a copy of the
  OpenAI adapter with a different base URL, not a subclass. The TypeScript SDK uses subclasses
  of about 40 lines instead.
- Meta-providers: litellm, langchain, and aws_bedrock. The Bedrock adapter is 728 lines because
  it formats messages and parses responses separately for each model family.

The base class removes `temperature` for reasoning models and renames `max_tokens` for gpt-5.
It finds these models by name, so the list needs maintenance (`mem0/llms/base.py:38-129`).

Structured output causes most of the per-provider code. mem0 always asks for
`response_format={"type": "json_object"}`. Ollama and Gemini translate it to their own JSON
option. The Anthropic, Bedrock, and LangChain adapters accept the argument and ignore it. On those
providers, extraction depends on the prompt and on text scraping. If the JSON does not parse, mem0
logs the error and stores no memories (`mem0/memory/main.py:979-986`).

The other libraries:

- graphiti takes client objects from the caller. It has native clients for OpenAI, Azure,
  Anthropic, Gemini, and Groq, and one `OpenAIGenericClient` for every OpenAI-compatible server.
  It drives structured output with a Pydantic model. On Anthropic, it forces a tool call.
- hindsight has the largest provider layer, about 15,000 lines. One `OpenAICompatibleLLM` class
  serves 14 provider names from a table of base URLs. Separate classes cover Anthropic, Gemini,
  litellm, and several subscription back ends.
- langmem writes no provider code. A model string goes to LangChain `init_chat_model`, and
  extraction uses forced tool calls.
- letta-code passes local models to the `pi-ai` library and remote models to a Letta server. It
  writes configuration, not adapters.

For hotmemory: the extractor and the embedder are callables that the caller supplies
(`src/hotmemory/contract.py:61`, `src/hotmemory/store.py:15`). The library never handles JSON
mode, tool formats, or reasoning parameters, so the mem0 failure "parse error becomes no facts"
cannot occur inside it. These options are not proven necessary:

- Ship one OpenAI-compatible extractor helper that takes a `client`. It covers most providers
  with no per-provider code.
- Offer litellm or LangChain shims as optional extras, not provider classes.
- Make an extractor parse failure raise an error, not return an empty list.
- The `Embedder` signature has no query or document role. If an asymmetric embedder is needed
  (Vertex task types, Cohere input types), that is a contract change.

## 11. Source pointers

- mem0 add: `mem0/memory/main.py:760-1220`. Search: `main.py:1393-1823`. Scoring:
  `mem0/utils/scoring.py`. Entities: `mem0/utils/entity_extraction.py`.
- mem0 prompt: `mem0/configs/prompts.py:468-944`. Builder: `prompts.py:1016-1062`.
- mem0 pgvector keyword search: `mem0/vector_stores/pgvector.py:386-404`.
- mem0 TypeScript: `mem0-ts/src/oss/src/memory/index.ts`, `utils/scoring.ts`.
- mem0 plugin core: `integrations/agent-plugin-core/python/memory_core.py`, `hook_runner.py`,
  `flush_worker.py`, `mcp_server.py`.
- hotmemory: `docs/contracts.md`, `docs/benchmarks.md`, `src/hotmemory/hotdata.py:771-826`,
  `bench/prompts.py:69-90`.
- RuntimeDB: `src/datafusion/bm25_table_function.rs`, `src/vector/mod.rs`,
  `src/engine/indexes.rs`.
