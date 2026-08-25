# 🔬 Fractal Retrieval Evaluation 📊

> **Question:** after adding `LOCAL / GLOBAL / DRIFT / AUTO`, how do we know the modes are useful rather than merely more architecture?

This document defines the bounded evaluation strategy for Fractal retrieval.

## 💡 Why this exists

The next useful step after introducing retrieval modes is **measurement, not another database or framework**.

Without evaluation, adding PostgreSQL, pgvector, another GraphRAG engine, GDS write-back, or a second index would only increase system complexity while leaving the core question unanswered:

> Does the current Graphiti + Neo4j retrieval choose the right context shape for the question being asked?

So Fractal first evaluates the architecture it already has.

## 🧭 Evaluation ladder

```text
                 🔬 RETRIEVAL EVALUATION
                          │
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
   🧪 Policy suite   🗄 Live corpus    🤖 Answer eval
     required CI       optional           optional
          │               │                │
   routing/shape     retrieval/latency   usefulness/cost
```

### Level 1 — 🧪 deterministic policy evaluation

Runs without OpenAI and without Neo4j.

Measures:
- whether `AUTO` selects the expected intent;
- whether `LOCAL` keeps local evidence-shaped context;
- whether `GLOBAL` keeps community context;
- whether `DRIFT` combines local + community context;
- whether the policy remains read-side and non-authoritative;
- policy overhead only.

It **does not** measure answer quality, truth, live database latency, or LLM quality.

### Level 2 — 🗄 live corpus retrieval evaluation

Future/optional evaluation against a controlled Neo4j corpus.

Useful metrics:
- Recall@K / hit rate for expected source UUIDs;
- MRR / rank of expected source;
- context coverage;
- irrelevant-context rate;
- retrieval latency p50/p95;
- result diversity;
- community contribution in GLOBAL/DRIFT.

This should use the same corpus and questions for all modes.

### Level 3 — 🤖 provider-backed answer evaluation

Optional and explicitly separate because it introduces provider variance and cost.

Useful metrics:
- grounded answer usefulness;
- citation/source coverage where available;
- unsupported-claim rate;
- token cost;
- end-to-end latency.

LLM judge output must not become truth or memory authority.

## 📋 Initial deterministic benchmark

The required suite contains both English and Russian query intents:

| Case | Expected mode | Why |
|---|---|---|
| 🔎 specific Reader decision | LOCAL | narrow event/entity question |
| 🌍 themes across whole corpus | GLOBAL | corpus-level synthesis |
| 🌀 why Crystal and Titan connect | DRIFT | local relation + broader pattern |
| 🔎 specific merge time | LOCAL | bounded historical lookup |
| 🌍 общие тенденции во всех проектах | GLOBAL | cross-corpus themes |
| 🌀 почему решения связаны | DRIFT | relational/explanatory query |

A policy regression fails CI if routing or context shape no longer matches the declared contract.

## ⚖️ Metrics are not authority

```text
high Recall@K       ≠ truth
high MRR            ≠ evidence strength
high community hit  ≠ community correctness
low latency         ≠ good answer
LLM judge preference ≠ factual authority
```

Evaluation helps choose retrieval behavior. It does not promote retrieved content into trusted memory.

## 🧠 Why we are not adding PostgreSQL / SQLite / pgvector here

Because the evaluation problem is not a storage problem.

Current active path:

```text
Fractal
   ↓
Graphiti hybrid retrieval
   ↓
Neo4j graph + episodes + communities
   ↓
LOCAL / GLOBAL / DRIFT policy
```

Adding another store before measuring this path would make attribution harder: if quality changes, we would no longer know whether the gain came from routing, indexing, storage, embeddings, or duplication.

PostgreSQL may later serve product/transactional state. SQLite may later serve lightweight durable jobs/checkpoints. Neither is required to answer the present retrieval-quality question.

## 🎯 Acceptance philosophy

A new retrieval mechanism should be considered only when the current stack has a **measured deficiency** that the candidate mechanism directly addresses.

In short:

> **measure → identify deficiency → choose mechanism → bounded experiment → compare → only then consider adoption.**
