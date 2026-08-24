# 🗺️ Fractal Memory — Human System Overview

> **Назначение:** подробная человеко-ориентированная карта Graphiti Fractal.
>
> Этот документ объясняет **зачем существует система, как она устроена, что уже реально работает, где проходят границы доверия и чем её архитектурный акцент отличается от других подходов к памяти AI**.
>
> 🤖 AI coding agents и автоматические аудиторы должны начинать не отсюда, а с [`docs/ai/README.md`](docs/ai/README.md).

[🏠 README](README.md) · [🤖 Special for AI](docs/ai/README.md) · [🧪 Technology Evolution](docs/TECHNOLOGY_EVOLUTION.md) · [🧠 AI Model Evolution](docs/AI_MODEL_EVOLUTION.md) · [🦞 OpenClaw patterns](docs/OPENCLAW_ADOPTED_PATTERNS.md)

---

## 👋 L0 — что такое Fractal за 30 секунд

**Fractal Memory** — local-first память для AI-агента поверх **Graphiti + Neo4j**.

Она не пытается быть вторым графовым движком и не объявляет всё найденное истиной. Graphiti отвечает за temporal/episodic graph semantics, Neo4j — за durable graph persistence, а Fractal добавляет безопасные границы вокруг ingestion, retrieval, namespaces, chat persistence, provenance и memory lifecycle.

```text
👤 человек / 🤖 агент
          │
          ▼
      💬 запрос
          │
          ▼
   🔎 scoped recall
          │
          ▼
     🕸️ Graphiti
          │
          ▼
      🗄️ Neo4j
          │
          ▼
  📦 bounded context
          │
          ▼
      🗣️ ответ
```

Главная идея проста:

> **Память должна помогать AI помнить и связывать прошлое, не превращая retrieval, популярность или красивый model output в скрытую власть над истиной.**

---

## 🧠 Mindmap — какие задачи решает система

```text
                         🧠 FRACTAL MEMORY
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
        ▼                      ▼                      ▼
   🕸️ Graph Memory       🔎 Retrieval           💬 Continuity
   temporal relations     scoped recall          chat persistence
        │                      │                      │
        ├──────────────┐       │              ┌───────┘
        ▼              ▼       ▼              ▼
   🧾 Provenance   🧩 Namespaces          🪜 L1 / L2 / L3
        │              │                       │
        └──────┬───────┴───────────┬───────────┘
               ▼                   ▼
         🛡️ Trust boundaries   🔁 Memory lifecycle
               │                   │
               ▼                   ▼
        imported ≠ trusted    recall / explain /
        graph ≠ truth         preview / promotion gate
```

---

## 🏗️ Архитектура одним взглядом

```text
┌──────────────────── 🌍 HUMAN / AGENT / TOOL ──────────────────────┐
│  🌐 Web UI · 🔌 HTTP API · 🤖 MCP stdio · ⌨️ CLI                  │
└───────────────────────────────┬────────────────────────────────────┘
                                ▼
                      🔐 Auth / local policy
                                │
                                ▼
                         🧠 MemoryOps
                                │
              ┌─────────────────┼──────────────────┐
              ▼                 ▼                  ▼
        ✍️ canonical ingest   🔎 recall       💬 chat persistence
              │                 │                  │
              └─────────────────┴──────────┬───────┘
                                          ▼
                                     🕸️ Graphiti
                                          │
                                          ▼
                                  🗄️ Neo4j 5.26 LTS
                                          │
                     ┌────────────────────┼────────────────────┐
                     ▼                    ▼                    ▼
                🧾 provenance       📊 telemetry          🪜 L1/L2/L3
```

### Ключевая граница

```text
🔎 retrieval proposes context
🧾 provenance preserves origin
🛡️ trust rules limit promotion
🤖 model generates language

retrieval ≠ evidence
frequency ≠ authority
imported ≠ trusted
graph ≠ truth
model output ≠ durable fact
```

---

## 🌳 Дерево системы

```text
🧠 Graphiti Fractal
│
├── 🌐 Product surfaces
│   ├── Web / HTTP
│   ├── MCP
│   └── CLI
│
├── 🧠 Memory core
│   ├── MemoryOps
│   ├── canonical ingest
│   ├── scoped retrieval
│   └── exact-UUID post-processing
│
├── 🧩 Memory namespaces
│   ├── personal
│   ├── project
│   ├── knowledge
│   ├── experience
│   └── imports  ← isolated / untrusted
│
├── 🔁 Lifecycle
│   ├── adaptive recall
│   ├── promotion explainability
│   ├── consolidation preview
│   └── external import isolation
│
├── 🪜 Derived views
│   ├── L1 episodic
│   ├── L2 communities
│   └── L3 bounded synthesis
│
├── 🛡️ Safety / integrity
│   ├── bearer-token data routes
│   ├── destructive ops off by default
│   ├── embedding fail-closed
│   └── unique ingest claim + exact UUID finalization
│
└── 🔬 Research / adjacent
    ├── GraphRAG / KAG / CAG
    ├── PostgreSQL / pgvector
    ├── alternative graph backends
    └── causal / GDS read-side experiments
```

---

## 🔄 Два главных потока

### 🔎 A. Recall / answer path

```text
👤 query
   ↓
🧭 recall policy: off | auto | always
   ↓
🧩 selected namespaces
   ↓
🔎 separate bounded Graphiti searches
   ↓
📦 merge + rank at application layer
   ↓
🗣️ model answer
```

Fractal не выполняет один скрытый unscoped query по всей памяти. Несколько namespaces ищутся отдельно, а результаты объединяются приложением.

### ✍️ B. Ingestion path

```text
📄 text
   ↓
✂️ semantic chunks
   ↓
🔍 namespace-scoped duplicate check
   ↓
🔐 unique FractalIngestClaim
   ↓
🕸️ Graphiti.add_episode()
   ↓
🎯 exact episode UUID
   ↓
🧾 fingerprint / group / authorship finalization
```

Concurrent duplicate protection закрывается на app-boundary unique claim. Это **не означает**, что внутренняя Graphiti transaction магически стала частью одной внешней транзакции.

---

## 📊 Что существует сегодня

| Область | Статус | Что это означает |
|---|---|---|
| 🕸️ Graphiti temporal graph memory | ✅ **ACTIVE** | основной memory semantics path |
| 🗄️ Neo4j 5.26 LTS | ✅ **ACTIVE** | основной durable graph backend |
| 🧩 Scoped namespaces | ✅ **ACTIVE** | `personal/project/knowledge/experience`; imports изолирован |
| 🔎 Adaptive recall | ✅ **ACTIVE** | `off / auto / always` без второго classifier LLM |
| 💬 Chat persistence | ✅ **ACTIVE** | persisted turns + bounded summary lifecycle |
| 🧾 Exact provenance for new derived artifacts | ✅ **ACTIVE** | source UUID lineage для новых summary/L3 paths |
| 🔐 Canonical ingest claim | ✅ **TESTED** | live Neo4j contract закрывает app-side duplicate race |
| 🔁 Promotion scoring | 🟡 **EXPLAIN / GATED** | считает eligibility; durable promotion writer не активирован |
| 🧪 Consolidation | 🟡 **DRY_RUN** | планирует, но не пишет автоматически |
| 📥 External imports | 🟡 **ISOLATED** | explicit apply, но origin остаётся untrusted |
| 🪜 L1/L2/L3 | ✅ / 🟡 | operational views; L3 — bounded synthesis, не Canon |
| 🕸️ GraphRAG / KAG / CAG | 🔬 **RESEARCH** | не параллельные active pipelines |
| 🗃️ PostgreSQL / pgvector | 🔬 **ADJACENT** | не active memory authority |
| 🐞 LadybugDB / Kuzu alternatives | 🔬 **RESEARCH** | migration не активирована |
| 🧮 Causal / GDS write-back | ❌ **NOT AUTHORIZED** | read-side research не равен runtime adoption |
| 🚀 Production authorization | ❌ **NOT CLAIMED** | green CI ≠ production authorization |

---

## 🧾 Как читать статусы

| Символ | Значение |
|---|---|
| ✅ | существует в текущем инженерном пути и подтверждено соответствующим контрактом |
| 🟡 | существует, но ограничено gate/preview/config или не имеет полной write authority |
| 🔬 | research / adjacent / proposed; наличие документа не делает это runtime |
| ❌ | отсутствует или намеренно не авторизовано |
| 🚧 | работа находится в open PR и ещё не main |
| ⚠️ | известное ограничение |

```text
📄 файл существует
   ≠ 🧪 поведение доказано
   ≠ 🎛️ функция включена
   ≠ 🔗 функция находится на active path
   ≠ 🚀 production разрешён
```

---

## 🆚 Чем архитектурный акцент отличается

> **Важно:** таблица сравнивает архитектурный фокус, а не объявляет Fractal «лучше всех». Эти системы могут решать разные задачи и могут использоваться совместно.

| Подход | 🎯 Основной фокус | 🕸️ Temporal graph | 🧾 Provenance emphasis | 🧩 Explicit trust isolation | 🔁 Promotion lifecycle | 🏠 Local-first boundary |
|---|---|---:|---:|---:|---:|---:|
| 📦 Vector RAG | relevant context retrieval | ❌ | 🟡 varies | 🟡 varies | ❌ usually outside scope | 🟡 |
| 🧠 Agent memory / Letta-style | agent continuity + memory management | 🟡 varies | 🟡 varies | 🟡 varies | ✅/🟡 | 🟡 |
| 🕸️ Graph memory systems | relation-aware memory/retrieval | ✅/🟡 | ✅/🟡 | 🟡 varies | 🟡 varies | 🟡 |
| 🕸️ Graphiti | temporal knowledge graph primitives | ✅ core | ✅ core | implementation-dependent | graph semantics, not Fractal policy | ✅/🟡 |
| 🧠 **Fractal Memory** | bounded local agent memory **on top of Graphiti** | ✅ inherited + bounded | ✅ explicit | 🎯 core boundary | 🎯 explain / preview / gated | 🎯 core goal |

Fractal **не заменяет Graphiti** — он использует его как основной memory engine и добавляет application-level contracts вокруг него. Также он не пытается заменить любой RAG, agent framework или relational database.

> 📅 Competitor capabilities меняются. Для архитектурных решений ориентируйтесь на актуальные upstream sources, а не на эту таблицу как вечный benchmark.

---

## 🧠 Memory lifecycle подробнее

### Adaptive recall

`FRACTAL_MEMORY_RECALL=off | auto | always`.

`auto` пропускает retrieval только для явно тривиальных turns; содержательные и неоднозначные запросы сохраняют bounded memory recall.

### Promotion gate

`core/memory_lifecycle.py` использует deterministic eligibility score и blockers. Высокая частота recall сама по себе не выдаёт authority. Origins `untrusted` и `system` остаются structurally ineligible.

Текущий promotion layer **объясняет eligibility, но не выполняет automatic durable promotion write**.

### Consolidation

```bash
python main.py memory-consolidate-preview candidates.json
```

Результат остаётся `DRY_RUN / writes_performed=false`.

### External memory imports

Preview:

```bash
python main.py memory-import ./memory.md --source-type openclaw
```

Explicit isolated import:

```bash
python main.py memory-import ./export.jsonl --source-type claude --apply
```

Applied material остаётся `untrusted`, попадает в namespace `imports` и не входит в normal chat recall.

---

## 💬 Chat persistence

`SimpleChatAgent` использует один answer path. Turns сохраняются через общий task registry. `turn_index` выделяется атомарно в Neo4j. Summary строится только после чтения реально persisted turn UUIDs; RAM buffer остаётся кратким L0 context и не становится durable identity source.

---

## 🪜 L1 / L2 / L3

```text
L1 🧠 recent episodic results
        ↓
L2 🕸️ Graphiti communities
        ↓
L3 🧩 bounded synthesis from L2 context
```

- **L1** — episodic results с временным окном.
- **L2** — Graphiti Communities.
- **L3** — derived synthesis с provenance; не authoritative fact и не отдельный graph authority.

---

## 🤖 Model policy

Current active defaults централизованы в `core/model_policy.py`, чтобы chat и Graphiti ingestion не расходились по hidden upstream defaults.

Подробности и история моделей: [`docs/AI_MODEL_EVOLUTION.md`](docs/AI_MODEL_EVOLUTION.md).

Embedding model не должен автоматически меняться вместе с chat model: это меняет identity векторного индекса и требует отдельного migration/reindex решения.

---

## 🧱 Technology map

| Technology | Роль | Текущий статус |
|---|---|---|
| Graphiti | temporal/episodic knowledge graph | ✅ ACTIVE |
| Neo4j | durable property graph | ✅ ACTIVE |
| PostgreSQL / pgvector | relational/vector adjacent path | 🔬 ADJACENT |
| GraphRAG / KAG / CAG | retrieval/reasoning research references | 🔬 RESEARCH |
| KV / prefix cache | inference compute reuse | ⚙️ INFERENCE LAYER |
| OpenClaw memory patterns | selected lifecycle ideas | 🟡 BOUNDED ADOPTION |

Evidence-backed evolution map: [`docs/TECHNOLOGY_EVOLUTION.md`](docs/TECHNOLOGY_EVOLUTION.md).

---

## 🛡️ Что система намеренно не утверждает

- ❌ retrieval match не является доказательством истины;
- ❌ graph relation не становится authority только потому, что она существует;
- ❌ imported memory не становится trusted из-за частоты использования;
- ❌ L3 synthesis не становится Canon;
- ❌ research dependency не становится active runtime dependency;
- ❌ green CI не означает production authorization;
- ❌ Fractal не является multi-user SaaS security boundary;
- ❌ provider-backed E2E не считается пройденным без реального provider secret и запуска.

---

## 🚀 Быстрый запуск

```bash
cp .env.example .env
# Укажите NEO4J_PASSWORD, OPENAI_API_KEY и FRACTAL_API_TOKEN.

docker compose build
docker compose up -d
```

Локальные endpoints:

- 🌐 Web/API — `http://127.0.0.1:8000`
- 🕸️ Neo4j Browser — `http://127.0.0.1:7474`
- 🔌 Bolt — `127.0.0.1:7687`

Destructive operations выключены по умолчанию через `FRACTAL_ALLOW_HARD_DELETE=0` и `FRACTAL_ALLOW_CLEAR_ALL=0`.

---

## 🧪 Validation layers

```text
⚙️ compile / unit contracts
          ↓
🕸️ provider-free live Neo4j integration
          ↓
🤖 provider-backed E2E (explicit secret required)
          ↓
🗄️ legacy provenance DRY_RUN (explicit legacy credentials)
```

Always-on CI и live Neo4j integration подтверждают внутренние contracts. Provider-backed E2E и legacy provenance preview являются отдельными external gates и не должны подменяться mock PASS.

---

## 📚 Куда читать дальше

| Если вам нужно… | Документ |
|---|---|
| 🤖 работать как AI coding agent / auditor | [`docs/ai/README.md`](docs/ai/README.md) |
| 🧠 понять историю model policy | [`docs/AI_MODEL_EVOLUTION.md`](docs/AI_MODEL_EVOLUTION.md) |
| 🧱 понять выбор технологий | [`docs/TECHNOLOGY_EVOLUTION.md`](docs/TECHNOLOGY_EVOLUTION.md) |
| 🦞 понять заимствованные memory patterns | [`docs/OPENCLAW_ADOPTED_PATTERNS.md`](docs/OPENCLAW_ADOPTED_PATTERNS.md) |
| 🔬 изучить research/adjacent paths | документы research/architecture в `docs/` и соответствующие PR |
| 🧪 проверить live acceptance evidence | GitHub Actions + exact-head PR evidence |

---

## 🧭 Документационный принцип

```text
                      🧠 ONE PROJECT REALITY
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
     👤 HUMAN VIEW         🤖 AI VIEW           📚 EVIDENCE
 README / OVERVIEW       docs/ai/README       tests / CI / PR
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                ▼
                       ⚙️ CURRENT CODE / STATE
```

Human documentation объясняет. AI documentation маршрутизирует. Tests/CI/PR evidence доказывают. Ни один красивый Markdown сам по себе не заменяет live repository state.
