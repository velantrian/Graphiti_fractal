# 📚 Fractal Memory — Historical Documentation Index

> **Status: HISTORICAL / NON-AUTHORITATIVE**
>
> Этот индекс сохраняет исходный путь разработки Fractal Memory и старые Day-by-Day документы как историю эволюции проекта. Он **не является инструкцией для текущего runtime**.
>
> Для текущего состояния используй корневой `README.md`, текущий код и CI contracts. Для эволюции AI-моделей см. `AI_MODEL_EVOLUTION.md`.

## 🕰️ Зачем оставлены старые документы

Ранние документы фиксируют, как проект развивался: от первоначальной 9-дневной схемы и custom entity экспериментов до текущей Graphiti-native архитектуры. Поэтому старые API-примеры, предположения, benchmark-цели и названия моделей **не стираются задним числом**.

В частности, историческое упоминание **GPT-4** в `Day_2_Custom_Entities.md` остаётся временным снимком своего этапа. Оно не означает, что GPT-4 является сегодняшним default. Аналогично любые старые упоминания других AI-моделей следует читать в контексте даты документа.

## ✅ Current authoritative entrypoints

| Что нужно | Источник |
|---|---|
| Текущая архитектура/runtime contract | `../README.md` |
| Текущая AI model policy | `../core/model_policy.py` |
| История моделей AI | `AI_MODEL_EVOLUTION.md` |
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

История ранних L1/L2/L3 концепций. Документ уже содержит historical warning; текущие L2/L3 существенно отличаются и используют Graphiti Communities + bounded LLM synthesis.

### Day 8–9 — Visualization & Performance
`Day_8_9_Visualization_Performance.md`

Исторические цели визуализации и performance-профилирования. Не воспринимать старые числа как текущие гарантии/SLO.

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

## 🤖 История AI-моделей

Отдельная страница `AI_MODEL_EVOLUTION.md` хранит две независимые части:

1. **Current policy snapshot** — активные Fractal defaults на указанную дату.
2. **Historical evolution** — GPT, Claude, Gemini, DeepSeek, Qwen, Grok и другие поколения как история развития экосистемы.

Ключевое правило:

> **Model mentioned ≠ provider supported ≠ runtime default.**

Сегодня first-class provider path Fractal Memory — OpenAI. Историческое присутствие Claude/Gemini/DeepSeek/Qwen/Grok в документации не создаёт их runtime adapter автоматически.

## 🧭 Как читать старый материал правильно

1. Сначала проверь дату и historical marker.
2. Не копируй старый код без сверки с текущими interfaces.
3. Старое имя модели воспринимай как snapshot эпохи.
4. Для текущей модели смотри `core/model_policy.py`.
5. Для текущей архитектуры смотри root `README.md` и CI.
6. Не превращай исторический future-work список в автоматически обязательный backlog.

Так проект сохраняет память о собственном развитии, но старые документы больше не конкурируют с текущей архитектурой. 🧠📜
