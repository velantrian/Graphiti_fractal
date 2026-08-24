# 🦞 OpenClaw → Fractal Memory — adopted patterns and boundaries

**Status:** CURRENT ARCHITECTURAL REFERENCE  
**Snapshot:** 2026-08-24  
**Runtime authority:** root `README.md` + current code/contracts

This document records which ideas were studied from OpenClaw and which were deliberately **not** imported.

Fractal Memory remains a Graphiti/Neo4j memory service. It is **not** becoming an OpenClaw gateway, channel router, multi-agent operating system, or plugin marketplace.

---

## 1. Sources and evidence

Primary OpenClaw sources checked:

- Memory CLI: https://docs.openclaw.ai/cli/memory
- Dreaming: https://docs.openclaw.ai/concepts/dreaming
- Memory architecture: https://github.com/openclaw/openclaw/blob/main/docs/concepts/memory-architecture.md
- Memory configuration: https://github.com/openclaw/openclaw/blob/main/docs/reference/memory-config.md

The OpenClaw behavior used as architectural reference includes:

- `memory status --deep` diagnostics;
- preview-first `memory promote` and explainable `promote-explain`;
- six-signal promotion ranking;
- minimum score + recall-count + unique-query gates;
- light → REM → deep staged consolidation;
- durable writes only in the deep phase;
- structural exclusion of `untrusted` and `system` candidates before promotion;
- re-reading live short-term material before write to avoid stale promotion.

Fractal copies **patterns**, not OpenClaw storage formats or agent runtime.

---

# 2. ✅ Adopted: adaptive pre-reply recall

Implementation:

- `core/memory_lifecycle.py::should_recall()`
- `FRACTAL_MEMORY_RECALL=off|auto|always`
- wired into `SimpleChatAgent.answer_core()`

Behavior:

```text
user query
   ↓
recall decision
   ├─ trivial + auto → skip Graphiti retrieval
   └─ substantive/ambiguous → bounded Graphiti retrieval
```

`auto` is intentionally conservative. It skips only clearly trivial turns such as greetings/thanks. It does **not** attempt an LLM-based intent classifier, so adaptive recall cannot silently add another model call before every answer.

Boundary:

- recall routing changes whether memory is fetched;
- it does not change memory truth or write authority.

---

# 3. ✅ Adopted: deterministic promotion gate + explainability

Implementation:

- `PromotionSignals`
- `explain_promotion()`
- CLI: `memory-promote-explain`

The six documented OpenClaw signal weights are retained for traceability:

| Signal | Weight |
|---|---:|
| relevance | 0.30 |
| frequency | 0.24 |
| query diversity | 0.15 |
| recency | 0.15 |
| consolidation | 0.10 |
| conceptual richness | 0.06 |

Current Fractal gate also requires:

- score ≥ `0.75`;
- recall count ≥ `3`;
- unique queries ≥ `3`;
- eligible origin class.

Every explanation includes:

- total score;
- per-signal contribution;
- thresholds;
- blockers;
- origin class;
- explicit `writes_performed: false`.

### Structural trust gate

`untrusted` and `system` origins are **ineligible before scoring can authorize promotion**.

This is critical for external data:

```text
web/import/system-derived content
       ↓
UNTRUSTED / SYSTEM
       ↓
can be retrieved as context
       ↓
CANNOT auto-promote to durable curated memory
```

High recall frequency does not convert untrusted input into owner-authoritative memory.

---

# 4. ✅ Adopted: staged consolidation — preview only in v0

Implementation:

- `plan_consolidation()`
- CLI: `memory-consolidate-preview <candidates.json>`

Fractal names the stages by function rather than copying sleep terminology:

```text
collect
   ↓
patterns
   ↓
promotion
```

The current implementation is deliberately **DRY_RUN only**.

Why:

OpenClaw has real recall/ingestion telemetry and scheduled phase state. Fractal does not yet persist equivalent recall-count/query-diversity telemetry. Automatically writing durable memory without those real measurements would create fake sophistication.

Therefore v0 provides:

- deterministic candidate evaluation;
- trust gates;
- explainability;
- a safe interface for later measured consolidation;
- **no automatic durable writer**.

A future writer must first add real recall telemetry and exact source identity, then pass a separate review.

---

# 5. ✅ Adopted: memory diagnostics

Implementation:

```bash
python main.py memory-status
python main.py memory-status --deep
```

Fast mode checks:

- required configuration;
- Neo4j connectivity;
- active model/embedding/recall configuration.

Deep mode additionally makes one semantic retrieval probe. It may therefore use the configured provider/embedding path.

Both modes are read-only:

```text
writes_performed = false
```

Unlike OpenClaw's `--fix`, Fractal diagnostics do not repair state implicitly.

---

# 6. ✅ Adopted: preview-first external memory import

Implementation:

```bash
python main.py memory-import ./export.md --source-type openclaw
python main.py memory-import ./export.json --source-type codex
python main.py memory-import ./export.jsonl --source-type claude --apply
```

Without `--apply` there are **no writes**.

Current supported generic input forms:

- Markdown/text;
- JSON;
- JSONL.

The parser conservatively extracts common content fields rather than claiming perfect vendor-specific export compatibility.

Every applied external import enters:

```text
group_id = imports
origin_class = untrusted
promotion_authorized = false
```

Limits:

- max file size: 10 MiB;
- max unique extracted entries: 5000;
- exact duplicate snippets are removed from the import plan;
- source SHA-256 is retained in the plan/source description.

### Important boundary

A label such as `--source-type codex` or `openclaw` is provenance metadata. It does **not** mean Fractal has a full semantic adapter for every historical version of that product's export schema.

---

# 7. ❌ Not adopted: OpenClaw gateway/channel system

Fractal does not copy:

- Telegram/WhatsApp/Discord channel routing;
- gateway/operator control plane;
- multi-agent workspace routing;
- broad plugin marketplace/runtime;
- cron/heartbeat orchestration;
- general tool-execution framework.

Those solve agent-runtime/product-surface problems rather than Fractal's bounded memory problem.

---

# 8. ❌ Not adopted: Markdown/SQLite as Fractal's memory authority

OpenClaw's memory architecture can use Markdown files and SQLite-backed indexing/state.

Fractal already has:

```text
Graphiti
   ↓
temporal/episodic knowledge graph
   ↓
Neo4j
```

Replacing this with `MEMORY.md` + SQLite would discard existing temporal/graph semantics.

Markdown remains suitable for export/readable projections, not for replacing the active memory authority.

---

# 9. ❌ Not adopted yet: scheduled automatic durable promotion

This is deferred until Fractal has real persisted telemetry for at least:

- recall count;
- distinct query identities;
- cross-day recurrence;
- source/origin identity;
- promotion history/idempotency;
- stale-candidate revalidation.

Future automatic promotion must also remain fail-closed for `untrusted` and `system` origins.

---

# 10. Tests / contracts

Always-on contracts cover:

- adaptive recall modes;
- substantive-query recall preservation;
- all deterministic promotion gates;
- structural `untrusted` exclusion even at maximum score;
- consolidation preview performing no writes;
- import preview performing no writes;
- applied imports remaining isolated in `imports`;
- import never authorizing promotion.

Relevant tests:

- `tests/test_memory_lifecycle.py`
- `tests/test_memory_import.py`

---

# 11. Historical record

**2026-08-24 — OpenClaw pattern adoption v0**

Added:

1. conservative adaptive recall;
2. deterministic/explainable promotion policy;
3. structural trust/origin gate;
4. dry-run staged consolidation planner;
5. read-only memory diagnostics;
6. preview-first isolated external import.

Deliberately deferred:

- automatic scheduled promotion;
- real recall telemetry store;
- vendor-specific import adapters;
- OpenClaw gateway/multi-agent/channel/tool runtime.

This distinction is part of the project history: **an external pattern can be adopted without importing the external system's entire architecture.**
