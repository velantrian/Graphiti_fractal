import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

import simple_chat_agent
from core import persist_outcomes
from core.safe_graphiti import filter_graphiti_results, safe_entity
from simple_chat_agent import SimpleChatAgent


class _Boom:
    @property
    def uuid(self):
        raise RuntimeError("driver exploded")


class _Res:
    def __init__(self, ents):
        self.extracted_entities = ents
        self.extracted_edges = []


@pytest.fixture(autouse=True)
def _clean_registry():
    persist_outcomes.clear()
    yield
    persist_outcomes.clear()


def test_filter_distinguishes_empty_error_ok():
    assert filter_graphiti_results(_Res([]))["status"] == "EMPTY"
    assert filter_graphiti_results(_Res([{"uuid": "u", "name": "n"}]))["status"] == "OK"
    err = filter_graphiti_results(_Res([_Boom()]))
    assert err["status"] == "DEGRADED_ERROR" and err["errored_entities"] == 1
    bad = filter_graphiti_results(_Res([{"uuid": None, "name": "n"}]))
    assert bad["status"] == "EMPTY" and bad["errored_entities"] == 0
    assert safe_entity(_Boom()) is None  # backward compatible


class _Memory:
    user_id = "u1"
    graphiti = object()


def _agent():
    a = SimpleChatAgent.__new__(SimpleChatAgent)
    a.memory = _Memory()
    a.last_persist_id = None
    return a


async def _idx(*a, **k):
    return 1


@pytest.mark.asyncio
async def test_persist_failure_is_reported_in_registry(monkeypatch):
    monkeypatch.setattr(simple_chat_agent, "allocate_turn_index", _idx)
    agent = _agent()

    async def boom(**k):
        raise RuntimeError("neo4j down")

    agent._persist_episode = boom
    out = await agent._persist_turn_pipeline(conversation_id="c", conversation_text="t", persist_id="chat:x")
    assert out["status"] == "failed" and "neo4j down" in out["error"]
    assert persist_outcomes.get("chat:x", user_id="u1")["status"] == "failed"


@pytest.mark.asyncio
async def test_timeout_during_episode_write_is_unknown(monkeypatch):
    monkeypatch.setattr(simple_chat_agent, "allocate_turn_index", _idx)
    agent = _agent()

    async def slow(**k):
        raise asyncio.TimeoutError()

    agent._persist_episode = slow
    out = await agent._persist_turn_pipeline(conversation_id="c", conversation_text="t", persist_id="chat:y")
    assert out["status"] == "unknown"


@pytest.mark.asyncio
async def test_timeout_in_allocate_before_any_write_is_failed(monkeypatch):
    async def idx_timeout(*a, **k):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(simple_chat_agent, "allocate_turn_index", idx_timeout)
    agent = _agent()
    agent._persist_episode = AsyncMock()
    out = await agent._persist_turn_pipeline(conversation_id="c", conversation_text="t", persist_id="chat:z")
    assert out["status"] == "failed"
    agent._persist_episode.assert_not_called()


def test_registry_is_bounded_lru(monkeypatch):
    monkeypatch.setattr(persist_outcomes, "MAX_ENTRIES", 3)
    for i in range(5):
        persist_outcomes.mark_pending(f"chat:{i}", user_id="u1")
    assert persist_outcomes.get("chat:0") is None and persist_outcomes.get("chat:1") is None
    assert persist_outcomes.get("chat:4")["status"] == "pending"


def test_registry_scoped_by_user():
    persist_outcomes.mark_pending("chat:a", user_id="u1")
    assert persist_outcomes.get("chat:a", user_id="u2") is None
    assert persist_outcomes.get("chat:a", user_id="u1") is not None


@pytest.mark.asyncio
async def test_parallel_documents_get_unique_ids_and_outcomes(monkeypatch):
    spawned = []
    monkeypatch.setattr(simple_chat_agent, "spawn", lambda coro, name=None: spawned.append(coro))
    calls = {"n": 0}

    async def ingest(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("second doc failed")
        return {"status": "ok"}

    monkeypatch.setattr("knowledge.ingest.ingest_text_document", ingest)
    monkeypatch.setattr(simple_chat_agent, "get_user_conversation_buffer", lambda uid: Mock())
    agent = _agent()
    ids = []
    for _ in range(2):
        await agent._handle_long_message("x" * 50)
        ids.append(agent.last_persist_id)
    assert ids[0] != ids[1]
    assert all(persist_outcomes.get(i)["status"] == "pending" for i in ids)
    for c in spawned:
        await c
    assert persist_outcomes.get(ids[0])["status"] == "persisted"
    assert persist_outcomes.get(ids[1])["status"] == "failed"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FRACTAL_API_TOKEN", "tok")
    monkeypatch.setenv("FRACTAL_USER_ID", "u1")
    from app import app
    with TestClient(app) as c:
        yield c


def test_persist_endpoint_reports_outcome_and_404_is_not_success(client):
    h = {"Authorization": "Bearer tok"}
    assert client.get("/persist/chat:missing", headers=h).status_code == 404
    persist_outcomes.record("chat:ok", {"status": "failed", "episode_uuid": None, "error": "E"}, user_id="u1")
    r = client.get("/persist/chat:ok", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "failed" and "user_id" not in r.json()
    persist_outcomes.record("chat:other", {"status": "persisted"}, user_id="someone-else")
    assert client.get("/persist/chat:other", headers=h).status_code == 404
    assert client.get("/persist/chat:ok").status_code in (401, 403)


@pytest.mark.asyncio
async def test_ingest_honors_degraded_error_status():
    from knowledge.ingest import ingest_text_document

    g = Mock()
    ep = Mock()
    ep.uuid = "ep-1"
    ep.episode.uuid = "ep-1"
    ep.extracted_entities = [_Boom(), {"uuid": None, "name": "n"}]
    ep.extracted_edges = []
    g.add_episode = AsyncMock(return_value=ep)
    g.driver = Mock()
    g.driver.execute_query = AsyncMock(return_value=Mock(records=[]))
    with (
        patch("knowledge.ingest.acquire_ingest_claim", new=AsyncMock(return_value="t")),
        patch("knowledge.ingest.mark_ingest_claim_episode_created", new=AsyncMock()),
        patch("knowledge.ingest.finalize_episode_identity", new=AsyncMock()),
        patch("knowledge.ingest.get_embedding", new=AsyncMock(return_value=None)),
    ):
        res = await ingest_text_document(g, "Short text", source_description="d")
    assert res["degraded_chunks"] == 1
    assert any("DEGRADED_ERROR" in w and "1 entities" in w for w in res["warnings"])
    assert any("dropped 1 entities" in w for w in res["warnings"])
