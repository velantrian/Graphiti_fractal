import pytest

from core.memory_ops import MemoryOps
from core.types import SearchResult


class Result:
    def __init__(self, records=None):
        self.records = records or []


class Driver:
    def __init__(self):
        self.queries = []

    async def execute_query(self, query, **kwargs):
        self.queries.append((query, kwargs))
        if "RETURN count(r) AS recorded" in query:
            return Result([{"recorded": len(kwargs["object_uuids"])}])
        return Result()


class Graphiti:
    def __init__(self):
        self.driver = Driver()


@pytest.mark.asyncio
async def test_build_context_records_uuid_usage_without_second_search(monkeypatch):
    graphiti = Graphiti()
    memory = MemoryOps(graphiti, "owner")
    calls = {"search": 0}

    async def fake_search(*args, **kwargs):
        calls["search"] += 1
        return SearchResult(
            entities=[{
                "uuid": "entity-1",
                "name": "Graphiti",
                "summary": "Temporal graph memory",
                "score": 0.9,
                "type": "entity",
                "group_id": "knowledge",
            }],
            total_entities=1,
        )

    monkeypatch.setattr(memory, "search_memory", fake_search)
    context = await memory.build_context_for_query("What is Graphiti?", max_tokens=100)

    assert calls["search"] == 1
    assert "Graphiti" in context.text
    telemetry_writes = [
        kwargs
        for query, kwargs in graphiti.driver.queries
        if "RETURN count(r) AS recorded" in query
    ]
    assert len(telemetry_writes) == 1
    assert telemetry_writes[0]["object_uuids"] == ["entity-1"]
