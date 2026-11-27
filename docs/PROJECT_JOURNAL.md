# 📓 Журнал проекта · Graphiti Fractal

**27 ноября 2026 · Рабочая графовая память · Graphiti + Neo4j**

[🤖 Вход для ИИ](ai/README.md) · [🗺️ Архитектура](../SYSTEM_OVERVIEW.md) · [🧪 Журнал Fractal Lab](https://github.com/velantrian/Graphiti_fractal_lab/blob/main/docs/PROJECT_JOURNAL.md)

## Где мы сейчас

Пакет целостности памяти вошёл в `main` в [`920761a`](https://github.com/velantrian/Graphiti_fractal/commit/920761a9cb4e63dd3517f58768422af2bfedd2fa). Последний проверенный коммит с изменениями памяти — [`8b7a8c8`](https://github.com/velantrian/Graphiti_fractal/commit/8b7a8c858efe774b6aca12ba17def1279251abf6).

| Статус | Что работает | Где разбираться |
|---|---|---|
| ✅ В `main` | Интеграционные тесты используют одноразовую Neo4j. Допуск проверяется до первой записи. | [#55](https://github.com/velantrian/Graphiti_fractal/pull/55) |
| ✅ В `main` | Статусы различают подтверждённую и неопределённую запись. Если Graphiti мог сохранить данные, повтор требует сверки. | [Диагностика ingest](INGEST_CLAIM_DIAGNOSTICS.md) · [#57](https://github.com/velantrian/Graphiti_fractal/pull/57) |
| ✅ В `main` | Удаление ограничивает затронутые объекты и при необходимости ставит запись в группу на паузу. Пересборка использует допустимые источники. | [Правила удаления и пересборки](ERASURE_VARIANT_B.md) · [#56](https://github.com/velantrian/Graphiti_fractal/pull/56) |
| ✅ В `main` · 27.11.2026 | Provider E2E допускается к одноразовой Neo4j через guard: маркер проверяется на том подключении, которое затем использует Graphiti, до схемы и записи; драйвер закреплён за допущенной базой (`DatabasePinnedDriver`). В правилах удаления исправлено противоречие: незавершённая попытка записи, пересёкшая удаление, ограничивает выдачу производных объектов группы и ставит её запись на паузу. | [Изоляция тестовой базы](TEST_DATABASE_ISOLATION.md) · [#60](https://github.com/velantrian/Graphiti_fractal/pull/60) · [коммит слияния `8168edb`](https://github.com/velantrian/Graphiti_fractal/commit/8168edb37a4158e536189185361987c25fae9b64) |
| ✅ В `main` · 27.11.2026 | D2. Поиск проверяет, что видимый объект с данным UUID однозначен, и сравнивает полный текст кандидата с текущей версией в Neo4j. Изменившиеся и неоднозначные кандидаты исключаются. | [Правила допуска](ERASURE_VARIANT_B.md#text-changed-between-search-and-admission-d2) · [#62](https://github.com/velantrian/Graphiti_fractal/pull/62) · [Neo4j CI](https://github.com/velantrian/Graphiti_fractal/actions/runs/45980288858) |
| ✅ В `main` · 27.11.2026 | MCP принимает проверяемую квитанцию для пересказа. Удаление следует по происхождению через группы и извлечённый граф. После потери ответа зависимые данные изолированы, а независимое удаление может завершиться. | [Контракт MCP](ERASURE_VARIANT_B.md#mcp-retellings-read-receipts-and-the-deletion-of-their-derivatives) · [#64](https://github.com/velantrian/Graphiti_fractal/pull/64) |

На HEAD #56 прошли контракты Python, сборка Docker и интеграция Neo4j. `provider-e2e` и `provenance-preview` были пропущены. [Neo4j CI](https://github.com/velantrian/Graphiti_fractal/actions/runs/45913768265) · [Внешние проверки](https://github.com/velantrian/Graphiti_fractal/actions/runs/45913768243)

Приёмка на `8168edb` (27.11.2026, одноразовая Neo4j 5.26.29 и вторая база с контрольной записью, без ключа провайдера): контракты 261 passed, 1 xfailed; guard 29 passed; обычный `pytest tests/e2e` без тестовых настроек пропускает тесты, с `FRACTAL_REQUIRE_TEST_NEO4J=1` завершается ошибкой, драйвер не создаётся; допуск e2e 2 passed, прогон с моделью пропущен; чужая база и чужой маркер отклонены до первой записи (5 проверок). Все 33 запроса Graphiti ушли в допущенную базу, 5 попыток назвать другую базу отклонены до драйвера, маркер цел. **Прогон с настоящей моделью не выполнялся.**

На коммите `8b7a8c8` в `main` прошли 176 интеграционных тестов Neo4j, контракты Python 3.10 и 3.12 и сборка Docker. Вызов реального LLM-провайдера пропущен. [Neo4j CI](https://github.com/velantrian/Graphiti_fractal/actions/runs/46003456180) · [CI](https://github.com/velantrian/Graphiti_fractal/actions/runs/46003456293)

## Откуда продолжить

| Статус | Следующее действие | Как понять, что готово |
|---|---|---|
| 🟡 Нужен ключ провайдера | Запустить вручную workflow «Provider E2E (Opt-In)» на `main`. | Тест `test_provider_backed_ingest_and_recall_roundtrip` выполнен, а не пропущен; результат записан со SHA. |
| 🟡 Проверка клиента MCP | Проверить на реальном OpenClaw передачу `read_receipt` в `derived_from`. После проверки клиентов решить, включать ли `FRACTAL_MCP_REQUIRE_READ_RECEIPT=1`. | Есть результат цикла «поиск → пересказ → удаление». Без квитанции режим совместимости допускает записи, которые могут пережить удаление источника. |
| ⏳ Перед внедрением | Запустить [сухой прогон](../scripts/erasure_dry_run.py) на изолированной копии рабочей базы. | Есть отчёт по старым `deleted=true`, пробелам в происхождении и незакрытым попыткам записи. |
| 🟡 Решение владельца | Определить судьбу старых меток и объектов без надёжной истории происхождения. | Владелец видит, какие объекты и группы затронет выбранная миграция. |
| 🔬 Отдельный этап | Оценить перенос правил удаления в Lab. | Выбранные правила подтверждены тестами для каждого заявленного хранилища. |

## Как обновлять журнал

После слияния или принятого решения обнови строку статуса. Укажи дату, PR или SHA, что проверено и какое действие требуется для следующего шага. Подробности работы механизма держи в профильном документе и дай на него ссылку.
