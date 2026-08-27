import json

import pytest

from core.memory_import import IMPORT_GROUP_ID, apply_import_plan, build_import_plan
from knowledge.retrieval import search_knowledge


def test_markdown_import_is_preview_untrusted_and_isolated(tmp_path):
    path = tmp_path / "memory.md"
    path.write_text("Important external note", encoding="utf-8")

    plan = build_import_plan(str(path), source_type="openclaw")

    assert plan["mode"] == "PREVIEW"
    assert plan["origin_class"] == "untrusted"
    assert plan["target_group_id"] == IMPORT_GROUP_ID
    assert plan["entry_count"] == 1
    assert plan["writes_performed"] is False


@pytest.mark.asyncio
async def test_preview_does_not_touch_memory(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"message": "hello"}), encoding="utf-8")
    plan = build_import_plan(str(path))

    class FailingMemory:
        async def ingest_pipeline(self, *args, **kwargs):
            raise AssertionError("preview must not write")

    result = await apply_import_plan(FailingMemory(), plan, apply=False)
    assert result["writes_performed"] is False
    assert "_payload" not in result


@pytest.mark.asyncio
async def test_apply_keeps_imports_namespace_and_untrusted_boundary(tmp_path):
    path = tmp_path / "export.jsonl"
    path.write_text(
        '\n'.join([
            json.dumps({"content": "first note"}),
            json.dumps({"content": "second note"}),
        ]),
        encoding="utf-8",
    )
    plan = build_import_plan(str(path), source_type="codex")
    calls = []

    class Memory:
        async def ingest_pipeline(self, text, **kwargs):
            calls.append((text, kwargs))
            return {"added": 1, "skipped": 0, "warnings": []}

    result = await apply_import_plan(Memory(), plan, apply=True)
    assert result["mode"] == "APPLIED"
    assert result["promotion_authorized"] is False
    assert result["added"] == 2
    assert all(kwargs["group_id"] == IMPORT_GROUP_ID for _, kwargs in calls)
    assert all(kwargs["source_description"].startswith("external_import:codex:") for _, kwargs in calls)


@pytest.mark.asyncio
async def test_default_knowledge_search_excludes_imports_namespace():
    captured = {}

    class Result:
        records = []

    class Driver:
        async def execute_query(self, query, **params):
            captured["query"] = query
            captured["params"] = params
            return Result()

    class Graphiti:
        driver = Driver()

    assert await search_knowledge(Graphiti(), "external note") == []
    assert captured["params"]["group_id"] is None
    assert captured["params"]["imports_group_id"] == IMPORT_GROUP_ID
    assert "$group_id = $imports_group_id" in captured["query"]
    assert "node.group_id <> $imports_group_id" in captured["query"]


@pytest.mark.asyncio
async def test_explicit_import_search_is_visibly_untrusted_and_non_authoritative():
    class Record(dict):
        pass

    class Result:
        records = [
            Record(
                kind="Episodic",
                uuid="import-1",
                group_id=IMPORT_GROUP_ID,
                name=None,
                summary=None,
                content="Imported external note",
                score=0.9,
            )
        ]

    class Driver:
        async def execute_query(self, query, **params):
            assert params["group_id"] == IMPORT_GROUP_ID
            assert params["imports_group_id"] == IMPORT_GROUP_ID
            return Result()

    class Graphiti:
        driver = Driver()

    items = await search_knowledge(Graphiti(), "external note", group_id=IMPORT_GROUP_ID)
    assert items == [
        {
            "kind": "Episodic",
            "uuid": "import-1",
            "group_id": IMPORT_GROUP_ID,
            "origin_class": "untrusted",
            "authoritative": False,
            "score": 0.9,
            "text": "Imported external note",
        }
    ]
