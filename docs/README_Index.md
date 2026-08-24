# 📚 Fractal Memory — Historical Documentation Index

> **Status: HISTORICAL / NON-AUTHORITATIVE**
>
> Этот индекс сохраняет исходный путь разработки Fractal Memory и старые Day-by-Day документы как историю эволюции проекта. Он **не является инструкцией для текущего runtime**.
>
> Для current state используй root `README.md`; для ролей/истории AI — `AI_MODEL_EVOLUTION.md`; для storage/retrieval/graph/cache evolution — `TECHNOLOGY_EVOLUTION.md`; для OpenClaw-derived memory lifecycle patterns — `OPENCLAW_ADOPTED_PATTERNS.md`.

## 🕰️ Зачем оставлены старые документы

Ранние документы фиксируют, как проект развивался: от первоначальной 9-дневной схемы и custom entity экспериментов до текущей Graphiti-native архитектуры. Поэтому старые API-примеры, предположения, benchmark-цели, названия моделей и технологические идеи **не стираются задним числом**.

Историческое упоминание **GPT-4** в `Day_2_Custom_Entities.md` остаётся временным снимком своего этапа. Аналогично старые database/retrieval/graph предположения следует читать в контексте даты документа.

## ✅ Current authoritative/reference entrypoints

| Что нужно | Источник |
|---|---|
| Текущая архитектура/runtime contract | `../README.md` |
| Текущая AI model policy | `../core/model_policy.py` |
| Роли + история AI/MoE/cache | `AI_MODEL_EVOLUTION.md` |
| Storage/graph/retrieval/cache technology map | `TECHNOLOGY_EVOLUTION.md` |
| OpenClaw-derived recall/promotion/import/diagnostic patterns | `OPENCLAW_ADOPTED_PATTERNS.md` |
| Текущая конфигурация | `../core/config.py` + `../.env.example` |
| Реальные contracts | `../tests/` + `../.github/workflows/ci.yml` |
| Реализация L1/L2/L3 | `../layers/` |
| Canonical ingest | `../knowledge/ingest.py` |
| Scoped memory/retrieval | `../core/memory_ops.py` |

## 📜 Historical development trail

### Day 2 — Custom Entities
`Day_2_Custom_Entities.md`

Сохраняет первоначальный эксперимент с custom entity types и старый GPT-4-era пример. Полезен для понимания происхождения идей, но код/API оттуда нельзя считать текущим без сверки.

### Day 3–4 — Visualization & Queries
`Day_3_4_Visualization_Queries.md`

История ранних Cypher/search/context-builder подходов. Современный retrieval path находится в текущем коде и использует bounded per-namespace search.

### Day 5–7 — Fractal Layers
`Day_5_7_Fractal_Layers.md`

История ранних L1/L2/L3 концепций. Текущие L2/L3 используют Graphiti Communities + bounded LLM synthesis.

### Day 8–9 — Visualization & Performance
`Day_8_9_Visualization_Performance.md`

Исторические цели визуализации и performance-профилирования. Старые числа не являются текущими гарантиями/SLO.

### Master Project Plan
`Master_Project_Plan.md`

Исходный 9-дневный план. Сохраняется как архитектурная летопись, а не как актуальный backlog.

### Testing / refactoring notes

- `TESTING_AND_SIMPLE_AGENT.md`
- `HANDS_ON_TESTING.md`
- `REFACTORING_CHANGELOG.md`
- `GRAPH_CONNECTIVITY.md`
- `memory_ops.md`

Использовать для истории решений и отладки старых состояний; при конфликте с current README/code/contracts побеждает current state.

## 🤖 История и роли AI

`AI_MODEL_EVOLUTION.md` фиксирует не только поколения GPT/Claude/Gemini/DeepSeek/Qwen/Grok, но и **зачем конкретные семейства/tiers могут использоваться**, а также:

- MoE как sparse model architecture;
- total vs active parameters;
- dense vs MoE deployment implications;
- KV cache / prefix cache / provider prompt cache;
- official-source facts отдельно от Reddit/community operator signal.

Ключевое правило:

> **Model mentioned ≠ provider supported ≠ runtime default ≠ architecture adopted.**

## 🧱 История и роли технологий

`TECHNOLOGY_EVOLUTION.md` разводит по слоям:

- Graphiti — temporal knowledge-graph memory semantics;
- Neo4j — active durable graph store;
- SQLite — embedded relational/local store;
- PostgreSQL — relational transactional/product state;
- pgvector — vector search inside PostgreSQL;
- RAG — retrieval pattern;
- GraphRAG — graph/community-aware retrieval;
- KAG — knowledge-structured/hybrid reasoning;
- CAG — reusable long-context/KV working-set pattern;
- KV/prefix/prompt cache — inference acceleration, **not durable memory**.

Current runtime update from this audit is deliberately small: Neo4j stays on compatible **5.26 LTS** but Docker is pinned to fresh patch **5.26.29-community**. Other modern technologies remain documented/evaluated until a concrete requirement justifies implementation.

## 🦞 OpenClaw pattern adoption

`OPENCLAW_ADOPTED_PATTERNS.md` records a bounded 2026-08-24 adoption of memory ideas from OpenClaw:

- adaptive pre-reply recall;
- deterministic promotion scoring + `promote-explain` style visibility;
- structural exclusion of `untrusted` / `system` candidates from promotion;
- staged consolidation as **dry-run preview**, not an automatic writer;
- read-only `memory-status` diagnostics;
- preview-first external import into isolated `imports` namespace.

It also records what was deliberately **not** copied: gateway/channels, multi-agent runtime, plugin marketplace, Markdown/SQLite as Fractal memory authority, and scheduled durable promotion before real recall telemetry exists.

## 🧭 Как читать старый материал правильно

1. Сначала проверь дату и historical marker.
2. Не копируй старый код без сверки с текущими interfaces.
3. Старое имя модели/версии воспринимай как snapshot эпохи.
4. Для текущей модели смотри `core/model_policy.py`.
5. Для текущей архитектуры смотри root `README.md` и CI.
6. Для современных adjacent/research technologies смотри `TECHNOLOGY_EVOLUTION.md`.
7. Для текущего memory-lifecycle слоя смотри `OPENCLAW_ADOPTED_PATTERNS.md`.
8. Не превращай historical/future-work список в автоматически обязательный backlog.
9. Forum/Reddit evidence считай operator signal, а не спецификацией.

Так проект сохраняет память о собственном развитии, но старые документы больше не конкурируют с текущей архитектурой. 🧠📜
