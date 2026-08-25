# 🦞 OpenClaw-inspired memory guards — bounded Fractal adaptation

This document records the small set of OpenClaw memory-control ideas adopted by Fractal without copying OpenClaw's storage/runtime architecture.

## Why this exists

Fractal already has its own memory substrate and authority model:

- Graphiti + Neo4j remain the only active graph/memory substrate;
- retrieval is not evidence;
- model output is not an independent confirmation;
- derived content is not authoritative fact;
- imports remain isolated/untrusted;
- no second memory database or GraphRAG engine is introduced here.

The useful OpenClaw ideas are therefore control-plane ideas, not a storage migration.

## 1. 🧾 Context Receipt

Every successful pre-reply recall can produce a non-authoritative receipt describing the exact rendered context text exposed to the model:

- query SHA-256 (never the raw query in durable recall metadata);
- rendered context SHA-256;
- retrieval mode and routing reason;
- source collection counts;
- token budget and estimate;
- truncation flag;
- status (`OK`, `DEGRADED_TIMEOUT`, `DEGRADED_ERROR`, `SKIPPED`).

A receipt is observability metadata only:

> context receipt != evidence != truth != Canon

### Exact source UUID limitation

The current `ContextResult` exposes exact rendered text and per-collection counts but does not preserve line-to-object UUID identity through final formatting/truncation. Therefore `ContextReceipt.source_ids` is intentionally empty unless a caller can supply exact model-visible UUIDs.

Fractal must never invent those UUIDs from pre-format retrieval results.

A later bounded formatter may add exact model-visible UUID tracking once it can prove line-level identity after truncation.

## 2. 🔁 Recall-derived turn guard

If a chat response was generated with non-empty recalled memory context, the persisted `chat_turn` episode is marked by exact UUID with:

- `recall_derived=true`;
- `recall_context_sha256`;
- `recall_query_sha256`;
- `recall_guard_authoritative=false`.

If recall was skipped, empty, timed out, or failed, the turn is **not** marked recall-derived.

This makes the following distinction explicit:

```text
memory A
  ↓ recalled
assistant repeats/uses A
  ↓
chat_turn(recall_derived=true)
```

That new turn is not an independent confirmation of A.

Existing Fractal behavior already reduces echo risk because ordinary `chat_turn` items are downweighted and excluded from normal rendered memory context unless they represent corrections. This guard adds explicit provenance rather than a competing filter.

## 3. ⏱️ Fail-soft recall timeout

Pre-reply memory retrieval has a bounded timeout controlled by:

```text
FRACTAL_RECALL_TIMEOUT_SECONDS
```

Default: `2.5` seconds.

Behavior:

```text
recall succeeds       → answer with memory
recall times out      → answer without memory + DEGRADED_TIMEOUT receipt
recall raises error   → answer without memory + DEGRADED_ERROR receipt
recall disabled       → answer without memory + SKIPPED receipt
```

Memory availability must not become answer-path availability.

## What was deliberately NOT copied

- no OpenClaw file-memory store;
- no SQLite semantic-memory replacement;
- no second memory authority;
- no plugin/context engine transplant;
- no autonomous dreaming writer;
- no deep-recall sub-agent yet;
- no automatic promotion/write-back from recalled content.

## Why deep recall is deferred

Fractal now has deterministic `AUTO / LOCAL / GLOBAL / DRIFT` routing and Retrieval Evaluation v1. A second/deeper retrieval lane should be activated only if measured live-corpus evaluation demonstrates a concrete recall deficiency that cannot be solved inside the existing Graphiti path.

The decision rule stays:

> measure → identify deficiency → bounded mechanism → compare → adopt only if justified
