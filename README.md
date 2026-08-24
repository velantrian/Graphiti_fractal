# 🧠 FRACTAL MEMORY 🕸️

> **Graphiti-native local-first memory for AI agents.**
>
> Представь AI, который не просто держит последние сообщения в контекстном окне, а может **помнить события во времени, связывать людей и идеи, возвращаться к прошлому опыту и сохранять происхождение памяти** — при этом не превращая найденное, часто повторяемое или сгенерированное моделью в автоматическую истину.
>
> Fractal строит именно такой bounded memory layer поверх **Graphiti + Neo4j**.

[🤖 **Special for AI / Agents**](docs/ai/README.md) · [🗺️ **Deep Human Overview**](SYSTEM_OVERVIEW.md) · [🧱 Technology Evolution](docs/TECHNOLOGY_EVOLUTION.md) · [🧠 AI Model Evolution](docs/AI_MODEL_EVOLUTION.md) · [🦞 OpenClaw Patterns](docs/OPENCLAW_ADOPTED_PATTERNS.md)

> 🤖 **AI coding agent / automated auditor:** не выводи current state из красивого human narrative. Начни с [`docs/ai/README.md`](docs/ai/README.md), затем проверь exact live code, tests и CI evidence.

---

## 👋 Fractal за 60 секунд

Обычный чат часто выглядит так:

```text
💬 prompt → 🤖 model → 🗣️ answer
```

Fractal добавляет между человеком и моделью долговременную память с явными границами:

```text
👤 query
   ↓
🧭 recall policy
   ↓
🧩 scoped namespaces
   ↓
🕸️ Graphiti temporal memory
   ↓
🗄️ Neo4j
   ↓
📦 bounded remembered context
   ↓
🤖 model
   ↓
🗣️ answer
```

### Простыми словами

Fractal похож на персональную библиотеку памяти, где:

- 🕸️ **Graphiti** связывает события, сущности и отношения во времени;
- 🗄️ **Neo4j** хранит граф долговременно;
- 🧩 **namespaces** не дают разным классам памяти незаметно смешаться;
- 🔎 **recall** ищет только в разрешённых областях;
- 🧾 **provenance** показывает, откуда произошли derived artifacts;
- 🛡️ **trust rules** не позволяют частоте или импорту автоматически стать authority;
- 🤖 **LLM** использует память, но не получает скрытого права объявлять свой вывод durable fact.

### Инженерным языком

Fractal — single-owner local-first memory service поверх `graphiti_core==0.29.3` и Neo4j 5.26 LTS с canonical ingestion, namespace-scoped retrieval, chat persistence, provenance, L1–L3 views и bounded memory lifecycle.

Он **не строит второй graph engine**: Graphiti остаётся основным temporal/episodic memory semantics layer.

---

## 🧭 Что открыть первым

| Если вы… | Начните здесь |
|---|---|
| 👤 впервые видите проект | этот README, затем [`SYSTEM_OVERVIEW.md`](SYSTEM_OVERVIEW.md) |
| 🤖 AI coding agent / auditor | [`docs/ai/README.md`](docs/ai/README.md) |
| 🧑‍💻 хотите понять архитектуру глубоко | [`SYSTEM_OVERVIEW.md`](SYSTEM_OVERVIEW.md) |
| 🧱 сравниваете технологии | [`docs/TECHNOLOGY_EVOLUTION.md`](docs/TECHNOLOGY_EVOLUTION.md) |
| 🧠 проверяете model/provider policy | [`docs/AI_MODEL_EVOLUTION.md`](docs/AI_MODEL_EVOLUTION.md) + `core/model_policy.py` |
| 🦞 изучаете memory lifecycle ideas | [`docs/OPENCLAW_ADOPTED_PATTERNS.md`](docs/OPENCLAW_ADOPTED_PATTERNS.md) |
| 🧪 проверяете, что реально доказано | tests + GitHub Actions + exact-head PR evidence |

---

## 🧠 Mindmap

```text
                           🧠 FRACTAL
                               │
       ┌───────────────────────┼───────────────────────┐
       ▼                       ▼                       ▼
  🕸️ MEMORY GRAPH         🔎 RECALL               💬 CONTINUITY
       │                       │                       │
  events / relations      scoped search          persisted turns
       │                       │                       │
       ├──────────────┐        │             ┌────────┘
       ▼              ▼        ▼             ▼
  🧾 Provenance   🧩 Namespaces          🪜 L1 / L2 / L3
       │              │                        │
       └──────────────┴──────────┬─────────────┘
                                 ▼
                         🛡️ TRUST BOUNDARY
                                 │
                                 ▼
                       🔁 MEMORY LIFECYCLE
```

---

## 🗺️ Архитектура одним взглядом

```text
┌──────────────────── 🌍 HUMAN / AGENT / TOOL ──────────────────────┐
│   🌐 Web UI · 🔌 HTTP · 🤖 MCP · ⌨️ CLI                           │
└───────────────────────────────┬────────────────────────────────────┘
                                ▼
                         🔐 local boundary
                                │
                                ▼
                           🧠 MemoryOps
                                │
               ┌────────────────┼────────────────┐
               ▼                ▼                ▼
        ✍️ canonical ingest   🔎 recall      💬 persistence
               │                │                │
               └────────────────┴────────┬───────┘
                                        ▼
                                   🕸️ Graphiti
                                        │
                                        ▼
                                🗄️ Neo4j 5.26 LTS
                                        │
                  ┌─────────────────────┼─────────────────────┐
                  ▼                     ▼                     ▼
             🧾 provenance         📊 telemetry          🪜 L1/L2/L3
```

### Главная формула

```text
Graphiti remembers relationships through time.
Fractal bounds how that memory enters, is recalled, and is trusted.
Neo4j persists the graph.
The model uses memory — it does not become memory authority by default.
```

---

## 🌳 Дерево проекта

```text
🧠 Fractal Memory
│
├── 🌐 Interfaces
│   ├── Web / HTTP
│   ├── MCP
│   └── CLI
│
├── 🕸️ Graph memory
│   ├── Graphiti
│   └── Neo4j
│
├── 🧩 Namespaces
│   ├── personal
│   ├── project
│   ├── knowledge
│   ├── experience
│   └── imports  ⚠️ isolated / untrusted
│
├── 🔎 Recall
│   └── off / auto / always
│
├── ✍️ Canonical ingest
│   └── knowledge/ingest.py
│
├── 🔁 Lifecycle
│   ├── promotion explainability
│   ├── consolidation preview
│   └── external import isolation
│
├── 🪜 Derived views
│   ├── L1 episodic
│   ├── L2 communities
│   └── L3 bounded synthesis
│
└── 🔬 Research / adjacent
    ├── GraphRAG / KAG / CAG
    ├── PostgreSQL / pgvector
    ├── alternative graph backends
    └── causal / GDS experiments
```

---

## 📊 Что есть, что ограничено, что исследуется

| Область | Сейчас | Смысл |
|---|---|---|
| 🕸️ Graphiti memory | ✅ **ACTIVE** | основной temporal graph memory engine |
| 🗄️ Neo4j 5.26 LTS | ✅ **ACTIVE** | durable graph persistence |
| 🧩 Namespace isolation | ✅ **ACTIVE** | scoped memory boundaries |
| 🔎 Adaptive recall | ✅ **ACTIVE** | `off / auto / always` |
| 💬 Chat persistence | ✅ **ACTIVE** | persisted turns + bounded summaries |
| 🧾 Provenance | ✅ **ACTIVE** | exact lineage для новых derived artifacts |
| 🔐 Unique ingest claim | ✅ **TESTED** | concurrent duplicate admission fail-closed at app boundary |
| 🔁 Promotion | 🟡 **EXPLAIN / GATED** | eligibility есть; automatic durable writer отсутствует |
| 🧪 Consolidation | 🟡 **DRY_RUN** | preview only |
| 📥 External imports | 🟡 **ISOLATED** | explicit apply; остаются untrusted |
| 🪜 L1 / L2 / L3 | ✅ / 🟡 | views/synthesis, не новый Canon |
| 🕸️ GraphRAG / KAG / CAG | 🔬 **RESEARCH** | не active parallel pipelines |
| 🗃️ PostgreSQL / pgvector | 🔬 **ADJACENT** | не current memory authority |
| 🐞 Alternative graph backend | 🔬 **RESEARCH** | migration не активирована |
| 🧮 Causal / GDS write-back | ❌ **NOT AUTHORIZED** | research ≠ runtime |
| 🚀 Production authorization | ❌ **NOT CLAIMED** | CI green ≠ production authorization |

---

## 🧾 Визуальная грамматика статусов

| Метка | Значение |
|---|---|
| ✅ **active / tested** | относится к текущему инженерному пути и подтверждено соответствующим contract evidence |
| 🟡 **bounded / gated** | существует, но ограничено preview/config/authority boundary |
| 🔬 **research / adjacent** | изучается; наличие кода или документа не означает runtime adoption |
| 🚧 **open PR** | ещё не является `main` |
| ⚠️ **limitation** | известная граница |
| ❌ **not authorized / unavailable** | нельзя утверждать как действующую capability |

```text
📄 file exists
   ≠ 🧪 contract proved
   ≠ 🎛️ feature enabled
   ≠ 🔗 active path
   ≠ 📡 runtime observed
   ≠ 🚀 production authorized
```

---

## 🆚 Чем Fractal отличается по архитектурному акценту

> Это **не рейтинг “кто лучше”**. Подходы решают разные задачи и могут использоваться вместе.

| Подход | 🎯 Главная задача | 🕸️ Temporal graph | 🧾 Provenance | 🛡️ Trust isolation | 🔁 Promotion lifecycle |
|---|---|---:|---:|---:|---:|
| 📦 Vector RAG | retrieve relevant context | ❌ | 🟡 varies | 🟡 varies | ❌ usually outside scope |
| 🧠 Agent memory / Letta-style | continuity + managed memory | 🟡 varies | 🟡 varies | 🟡 varies | ✅/🟡 |
| 🕸️ Graph memory | relation-aware memory | ✅/🟡 | ✅/🟡 | 🟡 varies | 🟡 varies |
| 🕸️ Graphiti | temporal knowledge-graph primitives | 🎯 core | 🎯 core | implementation-level | graph semantics |
| 🧠 **Fractal** | bounded local AI memory **on Graphiti** | ✅ | ✅ explicit | 🎯 core | 🎯 explain / preview / gated |

**Fractal не заменяет Graphiti.** Он использует Graphiti как основной memory engine и добавляет application-level boundaries: namespaces, canonical ingestion, trust isolation, lifecycle gates, product surfaces и validation contracts.

Подробное объяснение и ограничения сравнения → [`SYSTEM_OVERVIEW.md`](SYSTEM_OVERVIEW.md).

---

## 🛡️ Пять границ, которые важнее количества функций

```text
🔎 retrieval  ≠ evidence
🕸️ graph      ≠ truth
📥 imported   ≠ trusted
🔁 frequency  ≠ authority
🤖 model text ≠ durable fact
```

И ещё две инженерные:

```text
🔬 research ≠ runtime
✅ green CI ≠ production authorization
```

---

## 🧩 Memory namespaces

| Namespace | Для чего | Normal recall |
|---|---|---:|
| 👤 `personal` | локальная память владельца / диалога | ✅ |
| 🛠️ `project` | проекты и технические решения | ✅ |
| 📚 `knowledge` | документы и общие знания | ✅ |
| 🧪 `experience` | опыт выполнения задач | ✅ |
| 📥 `imports` | явно применённая внешняя память | ❌ isolated |

Для нескольких namespaces Fractal делает **отдельные bounded Graphiti searches**, затем объединяет результаты на application layer. Один скрытый global unscoped query не является canonical path.

---

## 🔁 Memory lifecycle

### 🧭 Adaptive recall

```text
query
  ↓
recall policy
  ├── off     → no memory recall
  ├── auto    → skip only clearly trivial turns
  └── always  → bounded recall
```

### ⚖️ Promotion gate

Deterministic scoring может объяснить eligibility и blockers, но **не выполняет automatic durable promotion write**.

`untrusted` и `system` origins не становятся eligible только из-за высокой частоты recall.

### 🧪 Consolidation

```bash
python main.py memory-consolidate-preview candidates.json
```

Всегда preview: `DRY_RUN / writes_performed=false`.

### 📥 External imports

```bash
# Preview — no write
python main.py memory-import ./memory.md --source-type openclaw

# Explicit write into isolated imports namespace
python main.py memory-import ./export.jsonl --source-type claude --apply
```

Applied import остаётся `untrusted` и не получает normal chat recall authority.

---

## 🪜 L1 / L2 / L3

```text
🧠 L1 — recent episodic memory
          ↓
🕸️ L2 — Graphiti communities
          ↓
🧩 L3 — bounded synthesis with provenance
```

L3 — derived representation, а не параллельный источник истины.

---

## 🤖 AI model policy

Current defaults централизованы в `core/model_policy.py`.

| Workload | Default |
|---|---|
| 💬 Interactive chat | `gpt-5.6-terra` |
| 🕸️ Graphiti extraction / reasoning | `gpt-5.6-terra` |
| 🧩 Summary synthesis | `gpt-5.6-luna` |
| ⚙️ Graphiti small prompts | `gpt-5.6-luna` |
| 🧭 Frontier opt-in | `gpt-5.6-sol` via env |
| 🔢 Embeddings | `text-embedding-3-small` |

First-class provider path сейчас OpenAI. История и роли других model families описаны отдельно в [`docs/AI_MODEL_EVOLUTION.md`](docs/AI_MODEL_EVOLUTION.md); упоминание модели там **не означает active runtime support**.

Embedding model не меняется автоматически вместе с chat model, потому что это меняет identity векторного индекса и требует отдельного reindex/migration решения.

---

## 🧱 Technology roles

| Technology | Role | Status |
|---|---|---|
| 🕸️ Graphiti | temporal/episodic graph memory | ✅ ACTIVE |
| 🗄️ Neo4j | durable graph backend | ✅ ACTIVE |
| 🗃️ PostgreSQL / pgvector | relational/vector alternatives | 🔬 ADJACENT |
| 🕸️ GraphRAG / KAG / CAG | retrieval/reasoning references | 🔬 RESEARCH |
| ⚡ KV / prefix cache | inference compute reuse | ⚙️ INFERENCE LAYER |
| 🦞 OpenClaw patterns | selected memory lifecycle ideas | 🟡 BOUNDED ADOPTION |

Evidence-backed details → [`docs/TECHNOLOGY_EVOLUTION.md`](docs/TECHNOLOGY_EVOLUTION.md).

---

## 🚀 Быстрый запуск

```bash
cp .env.example .env
# Заполните NEO4J_PASSWORD, OPENAI_API_KEY, FRACTAL_API_TOKEN.

docker compose build
docker compose up -d
```

Локально:

- 🌐 Web/API — `http://127.0.0.1:8000`
- 🕸️ Neo4j Browser — `http://127.0.0.1:7474`
- 🔌 Bolt — `127.0.0.1:7687`

Минимальная конфигурация:

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<strong-password>
OPENAI_API_KEY=<key>
FRACTAL_API_TOKEN=<long-random-token>
FRACTAL_USER_ID=<local-owner>
FRACTAL_MEMORY_RECALL=auto
```

Destructive operations выключены по умолчанию:

```env
FRACTAL_ALLOW_HARD_DELETE=0
FRACTAL_ALLOW_CLEAR_ALL=0
```

---

## 🛠️ Основные CLI команды

```bash
python main.py setup
python main.py seed
python main.py quality
python main.py context "Graphiti" --size full
python main.py benchmark
python main.py memory-status
python main.py memory-status --deep
python main.py memory-import ./memory.md --source-type openclaw
python main.py memory-promote-explain --help
python main.py memory-consolidate-preview candidates.json
python main.py l1 --query "Fractal Memory" --hours 24
python main.py l2 "Graphiti"
python main.py l3-build "Graphiti"
```

MCP stdio server:

```bash
python -m mcp_server
```

---

## 🧪 Как проверяется система

```text
⚙️ always-on Python contracts
           ↓
🕸️ live provider-free Neo4j integration
           ↓
🤖 provider-backed E2E
   requires real OPENAI_API_KEY
           ↓
🗄️ legacy provenance preview
   requires real legacy credentials
   DRY_RUN only
```

External test, который был skipped из-за отсутствующего secret, **не считается PASS**.

---

## ⚠️ Честные ограничения

- 🏠 система намеренно local/single-owner, не multi-user SaaS;
- 📥 applied imports остаются isolated/untrusted;
- 🔁 automatic durable promotion writer не активирован;
- 🧪 consolidation остаётся preview-first;
- 🤖 first-class provider path пока OpenAI;
- 🔬 GraphRAG/KAG/CAG/PostgreSQL/pgvector/alternative graph backends не являются скрытыми active dependencies;
- 🧮 causal/GDS research не имеет runtime write authority;
- 🚀 production authorization не следует из одного green CI.

---

## 📚 Читать глубже

### 👤 Для человека

➡️ **[`SYSTEM_OVERVIEW.md`](SYSTEM_OVERVIEW.md)** — подробная архитектурная экскурсия: flows, boundaries, lifecycle, comparisons, validation semantics и research map.

### 🤖 Для AI / Agents / Auditors

➡️ **[`docs/ai/README.md`](docs/ai/README.md)** — machine-first reading order, authority rules, invariants и forbidden inferences.

### 🧱 Для технического исследования

- [`docs/TECHNOLOGY_EVOLUTION.md`](docs/TECHNOLOGY_EVOLUTION.md) — technology decisions;
- [`docs/AI_MODEL_EVOLUTION.md`](docs/AI_MODEL_EVOLUTION.md) — model/provider evolution;
- [`docs/OPENCLAW_ADOPTED_PATTERNS.md`](docs/OPENCLAW_ADOPTED_PATTERNS.md) — adopted memory patterns.

---

## 🧭 Одна реальность — разные представления

```text
                       🧠 ONE PROJECT REALITY
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
          ▼                     ▼                     ▼
     👤 HUMAN VIEW         🤖 AI VIEW            📚 EVIDENCE
 README + OVERVIEW       docs/ai/README       tests / CI / PR
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                ▼
                       ⚙️ LIVE CODE / STATE
```

**Human docs объясняют. AI docs маршрутизируют. Evidence доказывает. Live code определяет текущую реализацию.**
