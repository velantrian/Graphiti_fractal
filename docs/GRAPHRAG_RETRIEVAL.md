# 🌐 Fractal GraphRAG Retrieval — why, what, and boundaries

**Status:** BOUNDED RETRIEVAL ENHANCEMENT  
**Memory authority:** unchanged — Graphiti + Neo4j  
**New database:** none  
**Second graph pipeline:** none

> The goal is not to install Microsoft GraphRAG beside Graphiti. The goal is to adopt the useful **query ideas** — local, global, and local↔global exploration — on top of the graph Fractal already owns.

---

## 💡 Why we implemented this

Fractal already had a strong temporal graph foundation:

```text
📝 episodes
   ↓
🕸️ Graphiti
   ├── entities
   ├── relationships
   ├── temporal validity
   └── communities
   ↓
🗄️ Neo4j
```

The missing piece was not another database. It was **retrieval intent**.

A question such as:

> “What did Alice say about project X?”

is local. It should prefer episodes, entities and nearby relationships.

A question such as:

> “What themes dominate everything I have stored?”

is global. Returning ten nearest entities is the wrong abstraction; community summaries are a better starting point.

A question such as:

> “Why are these projects connected, and what broader pattern explains that relationship?”

needs both. It should begin with local evidence and widen into relevant communities.

Previously all three questions entered essentially the same hybrid retrieval path. Graphiti's search was strong, but Fractal did not explicitly express these three different intents.

That is the reason for this enhancement.

---

## 🧠 The design choice

We deliberately chose:

```text
existing Graphiti + Neo4j
          ↓
   retrieval-mode policy
          ↓
 ┌────────┼────────┐
 ↓        ↓        ↓
🔎 LOCAL 🌍 GLOBAL 🌀 DRIFT
```

instead of:

```text
Graphiti + Neo4j
      +
second GraphRAG ingestion/index
      +
second graph / vector authority
```

Why?

1. 🛡️ **One memory authority.** Two graph pipelines can disagree about entities, relations, freshness and provenance.
2. 🧹 **Less duplication.** Graphiti already supplies entities, relationships, temporal episodes, hybrid search and communities.
3. 🔎 **The gap was query routing, not storage.** PostgreSQL, SQLite or another vector database would not solve global-vs-local retrieval intent.
4. 🕰️ **Temporal semantics stay intact.** Graphiti remains responsible for the temporal knowledge graph.
5. 🧪 **Easy to evaluate and remove.** A read-side routing layer can be benchmarked without migrating durable memory.

---

## 🔎 LOCAL mode

Use for entity/event-specific questions.

```text
query
  ↓
episodes + entities + relationships
  ↓
hybrid Graphiti ranking
  ↓
local context
```

Examples:

- What did I decide about X?
- Who is connected to Y?
- What happened after event Z?

Communities are intentionally not promoted into the final context by this mode.

---

## 🌍 GLOBAL mode

Use for corpus-level themes and broad patterns.

```text
query
  ↓
Graphiti communities
  ↓
community summaries
  ↓
global synthesis context
```

Examples:

- What are the main themes across my memory?
- What broad patterns recur across projects?
- What areas of knowledge dominate this corpus?

This is **GraphRAG-inspired**, not a claim that Fractal implements the complete Microsoft GraphRAG Global Search pipeline or its map/reduce community-report architecture.

---

## 🌀 DRIFT mode

Use when the question needs local detail plus broader context.

```text
                 query
                   │
          ┌────────┴────────┐
          ↓                 ↓
   local graph context   communities
          │                 │
          └────────┬────────┘
                   ↓
              fused context
```

Fractal uses the name **DRIFT** for a bounded GraphRAG-inspired local↔global retrieval policy. It must not be read as byte-for-byte or algorithm-for-algorithm equivalence with Microsoft GraphRAG DRIFT Search.

---

## 🧭 AUTO routing

`AUTO` is deterministic and intentionally conservative.

- corpus/theme cues → `GLOBAL`;
- explanatory/relationship cues → `DRIFT`;
- otherwise → `LOCAL`.

This is not an LLM authority decision. The router does not decide what is true; it only decides **which existing memory views are useful to retrieve**.

---

## 🛡️ Epistemic firewall

The following remain invariants:

```text
retrieval score     ≠ evidence strength
community           ≠ truth
community centrality ≠ real-world importance
local relation      ≠ causal relation
predicted link      ≠ stored fact
GraphRAG synthesis  ≠ memory authority
```

No LOCAL/GLOBAL/DRIFT result automatically writes to durable memory.

---

## 🗄️ Why PostgreSQL / SQLite were not added

### 🐘 PostgreSQL

PostgreSQL would be useful for relational operational state — users, permissions, durable jobs, billing, product metadata — if Fractal grows into that product shape.

It does **not** fix the retrieval problem addressed here. Adding PostgreSQL or pgvector now would duplicate persistence/retrieval responsibilities already served by Graphiti + Neo4j.

### 🪶 SQLite

SQLite remains a good future option for small local operational state such as durable queues or migration checkpoints.

It is deliberately not introduced as a second memory store.

### 🐞 LadybugDB

LadybugDB remains an interesting future lightweight graph-backend candidate, subject to Graphiti compatibility and parity testing. It is a backend choice, not a GraphRAG feature.

---

## 📊 What this adds vs what it does not

| Capability | Before | This enhancement |
|---|---|---|
| 🕸️ Temporal graph memory | ✅ | unchanged |
| 🔤 BM25/vector/graph hybrid retrieval | ✅ | unchanged foundation |
| 👥 Graphiti communities | ✅ | used more intentionally |
| 🔎 Explicit local mode | implicit | ✅ explicit |
| 🌍 Explicit global/community mode | partial/implicit | ✅ explicit |
| 🌀 Local↔global routing | ❌ | ✅ bounded DRIFT-inspired mode |
| 🧭 Deterministic AUTO router | ❌ | ✅ |
| 🗄️ New database | ❌ | ❌ deliberately not added |
| 🕸️ Second graph authority | ❌ | ❌ deliberately not added |
| ✍️ Retrieval write-back | ❌ | ❌ forbidden |
| Microsoft GraphRAG full implementation | ❌ | ❌ not claimed |

---

## 🧪 What should be measured next

The feature should earn its place through retrieval evaluation, not architecture enthusiasm.

Suggested evaluation set:

1. entity-specific/local questions;
2. whole-corpus/theme questions;
3. relationship/explanation questions;
4. temporal questions where community context must not erase time validity;
5. adversarial cases where a popular community is irrelevant to the query.

Compare:

```text
existing hybrid retrieval
        vs
AUTO / LOCAL / GLOBAL / DRIFT
```

Measure at least relevance, recall, context size, latency, and source/provenance coverage.

---

## 📚 Relationship to upstream GraphRAG

The architectural inspiration comes from the distinction between **Local Search, Global Search and DRIFT Search** in Microsoft GraphRAG. Fractal adopts the retrieval distinction because it matches capabilities already present in Graphiti; it does not import Microsoft GraphRAG as a runtime dependency.

For broader technology history and source links, see [`TECHNOLOGY_EVOLUTION.md`](TECHNOLOGY_EVOLUTION.md).
