from __future__ import annotations

import hashlib

import pytest

from core.context_guard import build_context_receipt, degraded_context, persist_recall_guard_metadata
from core.types import ContextResult


class _Result:
    def __init__(self, records):
        self.records = records


class _Driver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, **params):
        self.calls.append((query, params))
        return _Result([{"uuid": params["uuid"]}])


class _Graphiti:
    def __init__(self):
        self.driver = _Driver()


def test_receipt_hashes_exact_rendered_context_and_is_non_authoritative():
    context = ContextResult(
        text="## Эпизоды\n- Память: факт",
        token_estimate=8,
        sources={"episodes": 1, "entities": 0, "edges": 0, "communities": 0},
    )
    receipt = build_context_receipt(
        query="Что мы решили?",
        context=context,
        requested_mode="auto",
        effective_mode="local",
        reason="test",
        max_tokens=2000,
    )

    assert receipt.context_sha256 == hashlib.sha256(context.text.encode("utf-8")).hexdigest()
    assert receipt.source_counts["episodes"] == 1
    assert receipt.authoritative is False
    assert receipt.writes_performed is False
    assert receipt.source_ids == []


def test_degraded_context_is_empty_and_auditable():
    context = degraded_context(
        query="remember this",
        status="DEGRADED_TIMEOUT",
        reason="timeout",
        max_tokens=2000,
    )
    assert context.text == ""
    assert context.receipt is not None
    assert context.receipt.status == "DEGRADED_TIMEOUT"
    assert context.receipt.effective_mode == "none"
    assert context.receipt.authoritative is False


@pytest.mark.asyncio
async def test_persist_guard_marks_only_nonempty_successful_recall_as_derived():
    graphiti = _Graphiti()
    context = ContextResult(
        text="memory",
        token_estimate=2,
        sources={"episodes": 1, "entities": 0, "edges": 0, "communities": 0},
    )
    receipt = build_context_receipt(query="q", context=context, max_tokens=10)
    result = await persist_recall_guard_metadata(
        graphiti,
        episode_uuid="ep-1",
        context_receipt=receipt,
    )
    assert result["recall_derived"] is True
    assert result["authoritative"] is False
    params = graphiti.driver.calls[-1][1]
    assert params["recall_derived"] is True
    assert params["context_sha256"] == receipt.context_sha256


@pytest.mark.asyncio
async def test_persist_guard_does_not_mark_degraded_or_empty_context_as_derived():
    graphiti = _Graphiti()
    receipt = degraded_context(
        query="q",
        status="DEGRADED_ERROR",
        reason="boom",
        max_tokens=10,
    ).receipt
    result = await persist_recall_guard_metadata(
        graphiti,
        episode_uuid="ep-2",
        context_receipt=receipt,
    )
    assert result["recall_derived"] is False
    params = graphiti.driver.calls[-1][1]
    assert params["context_sha256"] is None
