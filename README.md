# 🧠 Fractal Memory — Graphiti-native local memory service

Fractal Memory — локальная single-tenant система памяти для AI-agent/assistant поверх **Graphiti + Neo4j**. Проект не строит второй graph engine: Graphiti отвечает за графовую/temporal модель, а Fractal добавляет bounded ingestion, namespace-scoped retrieval, chat persistence, HTTP/MCP/CLI surfaces и L1–L3 views.

## 🧭 Current architecture

```text
Web UI ─┐
HTTP API ├─> MemoryOps ─> Graphiti ─> Neo4j
MCP stdio┤       │
CLI ─────┘       ├─ canonical ingest: knowledge/ingest.py
                 └─ scoped retrieval: one search per group_id -> app-layer fusion
```

Основные инварианты:

- один локальный владелец задаётся `FRACTAL_USER_ID`;
- HTTP data routes закрыты Bearer-token `FRACTAL_API_TOKEN`;
- `personal`, `project`, `knowledge`, `experience` — отдельные `group_id` namespaces;
- multi-namespace retrieval выполняет отдельный Graphiti search в каждом namespace и объединяет результаты на уровне приложения;
- cross-namespace `SAME_AS` bridges не создаются и не используются active retrieval path;
- text ingestion проходит через один Graphiti-native pipeline (`knowledge/ingest.py`);
- post-processing эпизодов адресуется по точному UUID, а не по совпадающему тексту;
- hard delete и full clear выключены по умолчанию;
- embedding failure не подменяется нулевым вектором.

## 📦 Baseline

- Python: **3.10+** (always-on CI: 3.10 и 3.12)
- `graphiti_core==0.29.3`
- Neo4j: `>=5.26,<6`
- FastAPI + Uvicorn

## 🚀 Быстрый запуск

```bash
cp .env.example .env
# Заполни NEO4J_PASSWORD, OPENAI_API_KEY, FRACTAL_API_TOKEN.

docker compose build
docker compose up -d
```

Docker публикует сервисы только на localhost:

- Web/API: `http://127.0.0.1:8000`
- Neo4j Browser: `http://127.0.0.1:7474`
- Bolt: `127.0.0.1:7687`

Открой `http://127.0.0.1:8000/`. UI попросит `FRACTAL_API_TOKEN` и хранит его только в `sessionStorage` текущей вкладки.

### Локальный Python

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py setup
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

## 🔐 Environment

Минимально необходимые значения:

```env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<strong-password>
OPENAI_API_KEY=<key>
FRACTAL_API_TOKEN=<long-random-token>
FRACTAL_USER_ID=sergey
```

Destructive operations остаются выключенными:

```env
FRACTAL_ALLOW_HARD_DELETE=0
FRACTAL_ALLOW_CLEAR_ALL=0
```

Для HTTP API передавай:

```text
Authorization: Bearer <FRACTAL_API_TOKEN>
```

`/health`, корневой UI и static visualization доступны без токена; data-bearing routes требуют токен.

## 🧠 Memory model

| Namespace | Назначение |
|---|---|
| `personal` | локальная память владельца/диалога |
| `project` | архитектура, проекты, технические решения |
| `knowledge` | общие знания и документы |
| `experience` | структурированный опыт выполнения задач |

`MemoryOps.search_memory()` не делает общий unscoped query. Для нескольких namespaces выполняются отдельные bounded searches, после чего результаты объединяются и сортируются в приложении.

### Ingestion

`knowledge/ingest.py` — единственный canonical text-ingest path:

```text
text
 -> semantic chunks
 -> namespace-scoped exact duplicate check
 -> Graphiti.add_episode()
 -> exact episode UUID
 -> fingerprint / authorship / optional embedding by UUID
```

Одинаковый текст в разных `group_id` не считается автоматически одним и тем же memory object.

## 💬 Chat persistence

`SimpleChatAgent` имеет один answer path.

После ответа turn сохраняется best-effort background task через общий task registry. `turn_index` выделяется атомарно в Neo4j и не имеет fallback `1` при ошибке. На каждом 10-м turn summary строится только после чтения **10 реально persisted chat_turn UUIDs**; эти UUID затем явно отмечаются как summarized.

RAM conversation buffer используется только как краткий L0 context и не является источником идентичности persisted turns.

## 🪜 L1 / L2 / L3

- **L1** — недавние episodic results из canonical scoped retrieval с реальным `--hours` window.
- **L2** — Graphiti Communities через `(:Community)-[:HAS_MEMBER]->(:Entity)`.
- **L3** — bounded LLM synthesis из L2 context; отдельный legacy direct-Cypher consolidator удалён.

```bash
python main.py l1 --query "Fractal Memory" --hours 24
python main.py l2 "Graphiti"
python main.py l3-build "Graphiti"
python main.py l3 "Graphiti"
```

## 🛠️ CLI

```bash
python main.py setup
python main.py seed
python main.py quality
python main.py search-demo
python main.py context "Graphiti" --size full
python main.py benchmark
```

Destructive/cleanup commands fail safer:

```bash
# Только dry-run:
python main.py dedupe-entities
python main.py dedupe-episodes

# Явно применить soft-delete/merge:
python main.py dedupe-entities --apply
python main.py dedupe-episodes --apply

# Hard purge уже soft-deleted episodes — только явно:
python main.py dedupe-episodes --apply --purge-deleted-days 7

# Полная очистка требует точного подтверждения:
python main.py clear --confirm CLEAR_ALL_MEMORY
```

## 🔌 MCP

Локальный stdio server:

```bash
python -m mcp_server
```

Windows/Cursor может использовать `run_mcp_server.cmd` и `mcp.json.example`.

Current MCP tools:

- `memory.search_knowledge`
- `memory.search_experience`
- `memory.remember`
- `memory.upload`
- `memory.delete`

MCP writes всегда относятся к настроенному `FRACTAL_USER_ID`; клиент не выбирает произвольного владельца. Hard delete требует отдельного `FRACTAL_ALLOW_HARD_DELETE=1`.

## 🕸️ Visualization

```bash
python main.py viz-export
```

Генерируется ignored runtime-artifact `static/graph_data.json`. Viewer доступен через UI или `http://127.0.0.1:8000/visualization/visualization.html`.

## 🧪 Tests / CI

Always-on CI запускается на Python 3.10 и 3.12 и проверяет:

- compile active Python surface;
- MCP initialize + exact tool contract;
- fail-closed API auth;
- namespace-safe dedupe contracts;
- embedding fail-closed behavior;
- buffer clear contract;
- normalization/entities/experience hash unit tests.

```bash
pytest -q \
  tests/test_mcp_smoke.py \
  tests/test_security_contract.py \
  tests/test_dedupe_contract.py \
  tests/test_embedding_fail_closed.py \
  tests/test_buffer_clear_feature.py \
  tests/test_normalization.py \
  tests/test_entities.py \
  tests/test_experience_hash.py
```

Real Neo4j/OpenAI ingestion tests являются отдельным opt-in tier:

```bash
RUN_LLM_INGEST_TESTS=1 pytest -q tests/integration/test_chat_persistence.py
```

Green core CI означает только прохождение этих контрактов; он не заменяет live Neo4j/OpenAI integration validation.

## 🧹 Repository policy

Не коммитятся:

- `__pycache__`, `.pyc`;
- `.vscode`, local env/venv;
- Neo4j data/log/import/plugins;
- runtime `.log`;
- embedding cache;
- generated graph JSON.

Ручные HTTP smoke tools находятся в `scripts/`, а не в `tests/`, чтобы pytest не собирал их как тестовые функции.

## 📚 Documentation status

Этот `README.md` описывает **current runtime contract**.

Файлы `docs/Day_*`, старые master plans и refactoring notes — исторические материалы развития проекта. Они полезны как история решений, но не являются authoritative описанием текущего runtime. При конфликте ориентируйся на README + текущий код + CI contracts.

## ⚠️ Known bounded limitations

- сервис намеренно single-tenant/local, не multi-user SaaS;
- upload-job status хранится process-local и исчезает после restart;
- post-processing после `Graphiti.add_episode()` не является одной Neo4j transaction с внутренней Graphiti ingestion;
- concurrent exact-duplicate race между pre-check и завершением Graphiti write полностью не устранён;
- LLM/Neo4j integration tier требует внешние сервисы и не входит в always-on core CI.
