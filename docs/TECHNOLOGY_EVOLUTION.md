# 🧱 Fractal Technology Evolution — storage, graph, retrieval, caching

**Status:** CURRENT REFERENCE + HISTORICAL/RESEARCH MAP  
**Evidence snapshot:** 2026-08-24  
**Authority:** root `README.md` and current code remain authoritative for runtime behavior.

This document prevents a common architecture mistake: treating every modern data/AI technology as a replacement for every other one.

`SQLite`, `PostgreSQL`, `pgvector`, `Neo4j`, `Graphiti`, `RAG`, `GraphRAG`, `KAG`, `CAG`, `KV cache`, and provider prompt caches solve **different layers of the problem**.

> **Newer ≠ required. Mentioned ≠ installed. Research ≠ runtime. Retrieval ≠ memory truth. Cache ≠ persistence.**

## Status vocabulary

- **ACTIVE** — used by current Fractal runtime.
- **ADJACENT** — compatible/useful technology with a plausible role, but not active here.
- **RESEARCH** — worth understanding/evaluating; no implementation commitment.
- **HISTORICAL** — retained because it explains ecosystem/project evolution.
- **DEFERRED** — deliberately not added because current architecture does not need it.

---

# 1. 🗺️ Current stack by responsibility

```text
User / agent
    │
    ├── chat + HTTP + MCP + CLI
    │
    ├── Fractal bounded orchestration
    │       ├── namespaces / ownership
    │       ├── ingestion policy
    │       ├── scoped retrieval + fusion
    │       └── L1 / L2 / L3 views
    │
    ├── Graphiti
    │       ├── episodic / temporal knowledge-graph model
    │       ├── entity / relationship extraction
    │       ├── graph search
    │       └── communities
    │
    └── Neo4j 5.26 LTS
            └── durable graph persistence / Cypher / indexes

LLM inference is a separate plane:
OpenAI model → generation/extraction
KV/prompt cache → optional compute reuse, not durable memory
```

| Technology | Role | Fractal status |
|---|---|---|
| Graphiti 0.29.3 | temporal/episodic knowledge-graph memory framework | **ACTIVE** |
| Neo4j 5.26 LTS | durable property-graph database | **ACTIVE** |
| OpenAI embeddings | semantic vector representation | **ACTIVE** |
| Fractal scoped retrieval | bounded retrieval over Graphiti namespaces | **ACTIVE** |
| SQLite | embedded relational/local database | **DEFERRED** |
| PostgreSQL 18 | general relational transactional DB | **ADJACENT** |
| pgvector | vector similarity inside PostgreSQL | **ADJACENT** |
| classic RAG | retrieve chunks then generate | **CONCEPTUAL BASELINE / NOT A SECOND PIPELINE** |
| Microsoft GraphRAG | graph extraction + community summaries + graph-aware search | **RESEARCH** |
| KAG / OpenSPG | knowledge-graph + logical/hybrid retrieval/reasoning | **RESEARCH** |
| CAG | preload reusable corpus into model context/KV state | **RESEARCH** |
| KV/prefix/prompt cache | inference acceleration | **ADJACENT / PROVIDER OR SERVING LAYER** |

---

# 2. 🕸️ Graphiti — Fractal's active memory semantics layer

**ACTIVE: `graphiti_core==0.29.3`.**

Graphiti is not simply “a graph database wrapper” and not synonymous with Microsoft GraphRAG.

Its role in Fractal is to turn episodes into a **temporally aware knowledge graph** and expose retrieval over that graph. The project explicitly uses Graphiti as the semantic graph/memory layer instead of rebuilding a parallel graph engine.

Current Fractal responsibilities around it are narrower:

- namespace ownership (`group_id`);
- canonical ingestion path;
- exact-UUID post-processing;
- bounded per-namespace retrieval and application-layer fusion;
- chat persistence and L1/L2/L3 views.

**Current version decision:** 0.29.3 is already the current Graphiti release observed during this audit. No upgrade is required merely for freshness.

### Historical lesson

Earlier Fractal code accumulated direct Cypher and custom graph behavior that overlapped Graphiti. The current cleanup deliberately removed/contained those duplicate paths.

Therefore adding GraphRAG/KAG later must not recreate a second competing memory authority.

---

# 3. 🗄️ Neo4j — active durable graph store

**ACTIVE: Neo4j 5.26 LTS line.**

Neo4j's role is durable graph persistence and query/index infrastructure underneath Graphiti.

Fractal stays on the 5.26 LTS generation because Graphiti supports that line and a major/version-family jump should be compatibility-tested separately.

### 2026 freshness update

Neo4j **5.26.29** was released on 2026-08-04 and includes security fixes. Fractal therefore pins the Docker runtime to `neo4j:5.26.29-community` rather than the floating `5.26-community` tag.

This is a **patch-level hardening update**, not a graph-model migration.

### Why not jump automatically to the newest calendar-version Neo4j?

Because database freshness has two dimensions:

1. security/patch freshness;
2. dependency compatibility.

A newer family is not automatically safer for this application if Graphiti compatibility has not been validated.

---

# 4. 🪶 SQLite — embedded/local relational store

**Current upstream snapshot:** SQLite 3.53.4, released 2026-07-24.

SQLite remains highly relevant for:

- embedded metadata;
- local queues/state;
- small single-process relational datasets;
- tests and portable tooling.

SQLite 3.53.0 also fixed a serious WAL-reset corruption bug affecting older WAL-mode behavior under specific locking conditions. That is worth retaining in technology history because “small embedded DB” does not mean “maintenance-free”.

## Fractal decision

**DEFERRED.** Fractal already has Neo4j as its durable state store. Adding SQLite today would create a second persistence plane without a concrete missing requirement.

SQLite becomes justified only if Fractal gains state that is naturally relational/process-local and should **not** live in the knowledge graph — for example a durable local job queue or operational metadata store.

---

# 5. 🐘 PostgreSQL 18 — relational transaction/system-of-record candidate

**Current stable snapshot:** PostgreSQL **18.6**, released 2026-08-13.

PostgreSQL 18's major-generation changes included a new asynchronous I/O subsystem and database features such as `uuidv7()` and B-tree skip scan improvements.

Its natural role is different from Neo4j:

- transactions and constraints over relational records;
- operational/system-of-record data;
- SQL analytics;
- durable job/user/permission/product metadata;
- optional vector search via extensions such as pgvector.

## Fractal decision

**ADJACENT, not active.** PostgreSQL would become compelling if Fractal evolves from a local single-tenant memory service into a multi-user/product service with relational operational state.

It should not be added simply to duplicate Graphiti/Neo4j facts.

---

# 6. 🔢 pgvector — vector search *inside PostgreSQL*

**Current snapshot checked:** pgvector **0.8.6** (2026-08-20).

pgvector adds vector columns and similarity search to PostgreSQL, including approximate indexes such as **HNSW** and **IVFFlat**.

Its architectural role is useful when a product already needs PostgreSQL and wants:

```text
relational metadata + transactions + vectors
                in one database
```

## Fractal decision

**ADJACENT / DEFERRED.** Fractal already has Graphiti + Neo4j and an embedding/search path. Adding pgvector now would create another retrieval/index authority.

A future PostgreSQL migration could reassess pgvector at the same time; it should not arrive as an isolated “modern RAG feature”.

## Community signal

Reddit/PostgreSQL discussions report that pgvector can work at substantial scale but that HNSW memory, recall tuning, build time and operational requirements become important at very large vector counts. Other users move to dedicated vector engines when their scale/latency requirements outgrow a comfortable PostgreSQL setup.

**Interpretation:** benchmark with your corpus; do not choose a vector store from popularity alone.

---

# 7. 📚 Classic RAG — retrieval pattern, not one product

Classic Retrieval-Augmented Generation generally means:

```text
corpus
  ↓
chunk / index
  ↓
retrieve relevant passages for query
  ↓
put passages into model context
  ↓
generate answer
```

Its strengths:

- simple mental model;
- source-grounded context can be fetched on demand;
- corpus can change without retraining the LLM;
- efficient when only a small subset of a large corpus matters per query.

Its failure modes include:

- bad chunk boundaries;
- poor embeddings/index choice;
- missing cross-document relationships;
- retrieval recall/precision errors;
- retrieved text still does not become automatically true.

## Fractal relationship

Fractal already performs retrieval through Graphiti and scoped application fusion. It should **not** bolt on a second generic RAG pipeline unless an evaluation shows a concrete retrieval gap.

---

# 8. 🌐 Microsoft GraphRAG — graph-aware retrieval for global/local questions

Microsoft GraphRAG builds a graph from source text and organizes it into communities/community reports.

Its query modes include:

- **Local Search** — combines knowledge-graph data with source text for entity-oriented questions;
- **Global Search** — map/reduce over community reports for questions about the dataset as a whole;
- **DRIFT Search** — combines global/community context with local refinement;
- basic search modes for comparison/evaluation.

## What role it solves

GraphRAG is especially interesting when a question is not “find the nearest chunks” but:

- what themes dominate the whole corpus?
- how do entities/events connect across documents?
- what communities/topics emerge globally?

## Fractal decision

**RESEARCH, not installed.** Fractal already has Graphiti Communities and L2/L3 synthesis. The right next step is **evaluation against GraphRAG ideas**, not importing a second graph stack.

Possible bounded research questions:

- Would community-report style summaries improve L3?
- Would DRIFT-like local/global routing improve recall?
- Can those ideas be implemented using existing Graphiti communities without a parallel authority?

---

# 9. 🧠 KAG / OpenSPG — knowledge-augmented generation and hybrid reasoning

KAG (Knowledge Augmented Generation), associated with OpenSPG, goes beyond naive vector retrieval by combining structured knowledge with retrieval/reasoning techniques.

Its role is closer to:

- domain knowledge graphs;
- schema/semantic constraints;
- structured + unstructured retrieval;
- multi-hop/logical reasoning over knowledge.

## Why this is different from GraphRAG

GraphRAG primarily improves retrieval/synthesis through graph structure and communities over a corpus. KAG is more explicitly aimed at **knowledge-structured reasoning** and domain semantics.

## Fractal decision

**RESEARCH.** Fractal's Graphiti temporal graph already provides a structured knowledge substrate. KAG ideas are interesting only where they add something measurable — e.g. explicit logical-form routing or domain schema reasoning — without creating a competing truth/memory layer.

---

# 10. ⚡ CAG — Cache-Augmented Generation

The CAG research line proposes avoiding retrieval at query time when a reusable knowledge corpus can fit into a model's long context and its **KV cache can be precomputed/reused**.

Simplified:

```text
stable knowledge corpus
      ↓ one-time prefill
reusable KV/prefix state
      ↓
question 1 ─┐
question 2 ─┼─> generation without retrieval round-trip
question N ─┘
```

## Where CAG can be attractive

- corpus is bounded and relatively stable;
- it fits the supported context window;
- many queries reuse the same corpus;
- retrieval latency/variance is undesirable.

## Where RAG/Graph retrieval still wins

- corpus is too large;
- corpus changes frequently;
- queries need only tiny portions of a huge store;
- explicit source selection/provenance is important;
- persistent knowledge must outlive model/server caches.

## Fractal decision

**RESEARCH / complementary, not replacement.** A CAG-like cache could accelerate repeated analysis of a bounded working set, but Graphiti remains the durable memory/retrieval layer.

---

# 11. 🧮 KV cache / prefix cache / provider context cache

These are **inference-layer** technologies.

## KV cache

Stores attention key/value tensors for already processed tokens during autoregressive inference.

## Prefix cache

Serving engines can reuse KV blocks for a prefix already processed by another request.

vLLM's official documentation is explicit: Automatic Prefix Caching reduces **prefill** computation; it does **not** speed up decoding of newly generated tokens.

Typical wins:

- repeated queries against the same long document;
- multi-round conversations with a stable prefix;
- repeated stable system/tool instructions.

## Provider context/prompt caching

Hosted providers can implement the same general idea behind their API boundary. This can lower repeated-prefix cost/latency without exposing raw KV tensors.

## Fractal boundary

```text
cache eviction        → performance event
Neo4j/Graphiti delete → memory/state event
```

Those must never be conflated.

---

# 12. 🧭 What should actually change in Fractal now?

| Candidate | Decision | Reason |
|---|---|---|
| Graphiti 0.29.3 | **KEEP** | already current; active semantic memory layer |
| Neo4j `5.26-community` → `5.26.29-community` | **UPDATE** | patch-level security freshness while staying on supported LTS family |
| SQLite 3.53.x | **DOCUMENT ONLY** | no missing persistence role today |
| PostgreSQL 18.6 | **DOCUMENT / FUTURE PRODUCT PATH** | valuable if relational multi-user operational state appears |
| pgvector 0.8.6 | **DOCUMENT / FUTURE PG PATH** | avoid duplicate retrieval authority now |
| Microsoft GraphRAG | **RESEARCH IDEAS ONLY** | evaluate local/global/DRIFT/community-report concepts against existing Graphiti L2/L3 |
| KAG | **RESEARCH IDEAS ONLY** | evaluate structured logical reasoning only if a measurable domain need appears |
| CAG | **RESEARCH COMPLEMENT** | possible repeated-working-set accelerator, never durable memory replacement |
| KV/prefix cache | **INFERENCE OPTIMIZATION** | useful if/when Fractal self-hosts or explicitly manages provider caching |

This follows the project's simplicity rule: **modernize the part that has a concrete maintenance/security reason; document and evaluate the rest before adding machinery.**

---

# 13. 📜 Technology history policy

When an active dependency or architectural choice changes:

1. current README/runtime contract gets the new state;
2. this document records the previous state and why it changed;
3. old Day documents remain historical snapshots;
4. do not rewrite old plans as if the new technology had always existed;
5. record the migration/reindex/compatibility consequence, not only the new version number.

Examples:

- floating Neo4j `5.26-community` → pinned `5.26.29-community`: patch/security hardening, same LTS family;
- future embedding change: must record vector reindex consequence;
- future PostgreSQL adoption: must define which relational state moves there and which graph state remains in Neo4j;
- future GraphRAG/KAG/CAG experiment: must remain an evaluation until measured and explicitly adopted.

---

# 14. 🔗 Primary sources checked 2026-08-24

- Graphiti repository/releases: https://github.com/getzep/graphiti
- Neo4j 5.26.29 release notes: https://neo4j.com/release-notes/database/neo4j-5-26-29/
- Neo4j 5.26 LTS: https://neo4j.com/release-notes/database/neo4j-5-26-0/
- SQLite 3.53.4 chronology: https://sqlite.org/changes.html
- SQLite WAL-reset bug note: https://sqlite.org/wal.html
- PostgreSQL current version: https://www.postgresql.org/
- PostgreSQL 18 release: https://www.postgresql.org/about/news/postgresql-18-released-3142/
- pgvector releases/project: https://github.com/pgvector/pgvector
- Microsoft GraphRAG query overview: https://microsoft.github.io/graphrag/query/overview/
- Microsoft GraphRAG repository: https://github.com/microsoft/graphrag
- OpenSPG KAG repository: https://github.com/OpenSPG/KAG
- Cache-Augmented Generation paper: https://arxiv.org/abs/2412.15605
- vLLM Automatic Prefix Caching: https://docs.vllm.ai/en/latest/features/automatic_prefix_caching/

## Community signal checked

- pgvector scale discussion: https://www.reddit.com/r/PostgreSQL/comments/1ey3cwb/
- RAG implementation pain / chunking-quality discussion: https://www.reddit.com/r/Rag/comments/1s66rsl/

Community material is intentionally separated from primary-source facts.