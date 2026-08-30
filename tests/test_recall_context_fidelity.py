import asyncio
from types import SimpleNamespace

from core.context_guard import build_context_receipt
from core.memory_ops import MemoryOps


class _Graphiti:
    async def search_(self, **kwargs):
        scope = kwargs["group_ids"][0]
        return SimpleNamespace(
            episodes=[],
            episode_reranker_scores=[],
            nodes=[
                SimpleNamespace(uuid="node-a", name="A", summary="x" * 300, group_id=scope),
                SimpleNamespace(uuid="node-b", name="B", summary="y" * 300, group_id=scope),
            ],
            node_reranker_scores=[0.9, 0.8],
            edges=[],
            edge_reranker_scores=[],
            communities=[],
            community_reranker_scores=[],
        )


def test_telemetry_and_receipt_use_only_fully_exposed_sources(monkeypatch):
    recorded = {}

    async def fake_record_recall(graphiti, *, user_id, query, object_uuids):
        recorded["ids"] = list(object_uuids)
        return {"recorded": len(recorded["ids"]), "authoritative": False}

    monkeypatch.setattr("core.memory_ops.record_recall", fake_record_recall)
    memory = MemoryOps(_Graphiti(), "owner")
    context = asyncio.run(
        memory.build_context_for_query(
            "important context",
            scopes=["knowledge"],
            max_tokens=100,
        )
    )

    assert "[Контекст обрезан по лимиту]" in context.text
    assert context.source_ids == ["node-a"]
    assert context.sources == {
        "episodes": 0,
        "entities": 1,
        "edges": 0,
        "communities": 0,
    }
    assert recorded["ids"] == ["node-a"]

    receipt = build_context_receipt(
        query="important context",
        context=context,
        max_tokens=100,
    )
    assert receipt.source_ids == ["node-a"]
    assert receipt.source_counts["entities"] == 1
    assert receipt.truncated is True


def test_partially_rendered_source_line_is_not_counted_as_exposed(monkeypatch):
    recorded = {"called": False}

    async def fake_record_recall(graphiti, *, user_id, query, object_uuids):
        recorded["called"] = True
        return {"recorded": len(object_uuids), "authoritative": False}

    monkeypatch.setattr("core.memory_ops.record_recall", fake_record_recall)
    memory = MemoryOps(_Graphiti(), "owner")
    context = asyncio.run(
        memory.build_context_for_query(
            "important context",
            scopes=["knowledge"],
            max_tokens=4,
        )
    )

    assert "[Контекст обрезан по лимиту]" in context.text
    assert context.source_ids == []
    assert context.sources["entities"] == 0
    assert recorded["called"] is False
