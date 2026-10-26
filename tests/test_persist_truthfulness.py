"""Persist status must match what was actually stored (2026-10-26)."""
from unittest.mock import AsyncMock, Mock, patch

import pytest

import simple_chat_agent
from core import persist_outcomes
from simple_chat_agent import SimpleChatAgent


@pytest.fixture(autouse=True)
def _clean():
    persist_outcomes.clear()
    yield
    persist_outcomes.clear()


def _agent():
    agent = SimpleChatAgent.__new__(SimpleChatAgent)
    agent.memory = Mock(user_id="u1", graphiti=Mock())
    agent.last_persist_id = None
    return agent


async def _run_doc(monkeypatch, summary):
    spawned = []
    monkeypatch.setattr(simple_chat_agent, "spawn", lambda coro, name=None: spawned.append(coro))
    monkeypatch.setattr(simple_chat_agent, "get_user_conversation_buffer", lambda uid: Mock())
    monkeypatch.setattr("knowledge.ingest.ingest_text_document", AsyncMock(return_value=summary))
    agent = _agent()
    await agent._handle_long_message("x" * 50)
    pid = agent.last_persist_id
    assert persist_outcomes.get(pid)["status"] == "pending"
    for c in spawned:
        await c
    return persist_outcomes.get(pid)


@pytest.mark.asyncio
@pytest.mark.parametrize("summary, expected", [
    ({"status": "ok", "chunks": 3, "added": 3, "skipped_existing": 0, "skipped_claim_held": 0,
      "failed_chunks": 0, "degraded_chunks": 0, "completeness": "complete", "warnings": []}, "persisted"),
    ({"status": "ok", "chunks": 3, "added": 2, "skipped_existing": 0, "skipped_claim_held": 0,
      "failed_chunks": 1, "degraded_chunks": 0, "completeness": "partial",
      "warnings": ["Chunk 2: RuntimeError: neo4j down"]}, "partial"),
    ({"status": "error", "chunks": 3, "added": 0, "skipped_existing": 0, "skipped_claim_held": 0,
      "failed_chunks": 3, "degraded_chunks": 0, "completeness": "failed", "warnings": ["x"]}, "failed"),
    ({"status": "ok", "chunks": 3, "added": 1, "skipped_existing": 0, "skipped_claim_held": 2,
      "failed_chunks": 0, "degraded_chunks": 0, "completeness": "unconfirmed", "warnings": []}, "unconfirmed"),
    (None, "unknown"),
])
async def test_document_status_matches_stored_chunks(monkeypatch, summary, expected):
    entry = await _run_doc(monkeypatch, summary)
    assert entry["status"] == expected
    if summary:
        assert entry["chunks"]["added"] == summary["added"]
        assert entry["chunks"]["failed_chunks"] == summary["failed_chunks"]


def _graphiti_failing_chunk(fail_index):
    g = Mock()
    calls = {"n": 0}

    async def add_episode(**kw):
        calls["n"] += 1
        if calls["n"] == fail_index:
            raise RuntimeError("storage unavailable")
        ep = Mock()
        ep.episode.uuid = f"ep-{calls['n']}"
        ep.extracted_entities, ep.extracted_edges = [], []
        return ep

    g.add_episode = add_episode
    return g


@pytest.mark.asyncio
@pytest.mark.parametrize("claim, exists, fail, completeness, status", [
    ("t", False, None, "complete", "ok"),
    ("t", False, 2, "partial", "ok"),        # legacy status stays "ok": completeness tells the truth
    ("t", False, "all", "failed", "error"),
    (None, False, None, "unconfirmed", "ok"),  # every claim held by another writer: not "failed"
    ("t", True, None, "complete", "ok"),     # already stored earlier
])
async def test_ingest_completeness_real_loop(claim, exists, fail, completeness, status):
    from knowledge.ingest import ingest_text_document
    text = "\n\n".join(f"Параграф {i}. " + ("слово " * 60) for i in range(6))
    g = _graphiti_failing_chunk(fail if isinstance(fail, int) else -1)
    if fail == "all":
        g.add_episode = AsyncMock(side_effect=RuntimeError("storage unavailable"))
    with (
        patch("knowledge.ingest.episode_exists", new=AsyncMock(return_value=exists)),
        patch("knowledge.ingest.acquire_ingest_claim", new=AsyncMock(return_value=claim)),
        patch("knowledge.ingest.release_ingest_claim", new=AsyncMock()),
        patch("knowledge.ingest.mark_ingest_claim_episode_created", new=AsyncMock()),
        patch("knowledge.ingest.finalize_episode_identity", new=AsyncMock()),
        patch("knowledge.ingest.get_embedding", new=AsyncMock(return_value=None)),
    ):
        res = await ingest_text_document(g, text, source_description="d")
    assert res["chunks"] > 1
    assert res["completeness"] == completeness
    assert res["status"] == status
    assert res["added"] + res["skipped_existing"] + res["skipped_claim_held"] + res["failed_chunks"] == res["chunks"]


def test_registry_restart_loses_outcomes_and_404_is_not_absence():
    """Documents what the in-memory registry loses on restart (no persistence claimed)."""
    import importlib
    persist_outcomes.record("document:abc", {"status": "partial"}, user_id="u1")
    persist_outcomes.mark_pending("chat:p", user_id="u1")
    fresh = importlib.reload(persist_outcomes)  # same as a new process: module state rebuilt
    assert fresh.get("document:abc") is None   # final partial status gone
    assert fresh.get("chat:p") is None         # in-flight pending gone; its task died with the process


@pytest.mark.parametrize("args, expected", [
    # total, added, existing, failed, degraded, claim_held
    ((3, 3, 0, 0, 0, 0), "complete"),
    ((3, 1, 2, 0, 0, 0), "complete"),
    ((3, 0, 0, 0, 0, 3), "unconfirmed"),   # all held by another writer: never "failed"
    ((3, 2, 0, 0, 0, 1), "unconfirmed"),   # rest confirmed, one held elsewhere
    ((3, 0, 0, 2, 0, 1), "unconfirmed"),   # nothing confirmed but one may still land
    ((3, 1, 0, 1, 0, 1), "partial"),       # a definite failure alongside stored chunks
    ((3, 2, 0, 1, 0, 0), "partial"),
    ((3, 2, 0, 0, 1, 0), "partial"),
    ((3, 0, 0, 3, 0, 0), "failed"),
])
def test_completeness_claim_held_is_unconfirmed_not_failed(args, expected):
    from knowledge.ingest import _completeness
    assert _completeness(*args) == expected
