import asyncio
from types import SimpleNamespace

import pytest

from core.context_guard import build_context_receipt
from core.memory_ops import MemoryOps


class _AllFailGraphiti:
    async def search_(self, **kwargs):
        raise RuntimeError(f"scope unavailable: {kwargs['group_ids'][0]}")


class _PartialGraphiti:
    async def search_(self, **kwargs):
        scope = kwargs["group_ids"][0]
        if scope == "personal":
            raise RuntimeError("personal unavailable")
        return SimpleNamespace(
            episodes=[],
            episode_reranker_scores=[],
            nodes=[SimpleNamespace(uuid="node-1", name="Entity", summary="ok", group_id=scope)],
            node_reranker_scores=[0.9],
            edges=[],
            edge_reranker_scores=[],
            communities=[],
            community_reranker_scores=[],
        )


def test_all_requested_scope_failures_raise_instead_of_looking_empty():
    memory = MemoryOps(_AllFailGraphiti(), "owner")

    with pytest.raises(RuntimeError, match="all requested scopes"):
        asyncio.run(memory.search_memory("important context", scopes=["personal", "knowledge"]))


def test_partial_scope_failure_preserves_results_and_structured_degradation():
    memory = MemoryOps(_PartialGraphiti(), "owner")

    result = asyncio.run(
        memory.search_memory("important context", scopes=["personal", "knowledge"])
    )

    assert result.entities
    assert result.entities[0]["group_id"] == "knowledge"
    assert result.failed_scopes == ["personal"]

    context = asyncio.run(
        memory.build_context_for_query("important context", scopes=["personal", "knowledge"])
    )
    assert context.failed_scopes == ["personal"]

    receipt = build_context_receipt(
        query="important context",
        context=context,
        status="OK",
        reason="auto retrieval",
        max_tokens=4000,
    )
    assert receipt.status == "DEGRADED_PARTIAL"
    assert "personal" in receipt.reason
    assert receipt.authoritative is False
