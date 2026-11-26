"""Scenario C (PR #54 verification): chat while memory is deleted.

Real: SimpleChatAgent.answer_core, the RAM conversation buffer, MemoryOps recall,
the erasure state/fence on Neo4j, graphiti_core add_episode for the background save
(model replaced by RecordingLLM), finalize_chat_episode, HTTP /delete.
Simulated: the chat provider (a recorder that keeps what was sent) and the timing
(asyncio events placed at the three decision points and an optional DB outage).
What the provider already received cannot be taken back; it is reported separately.
"""
import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from graphiti_core import Graphiti
from httpx import ASGITransport, AsyncClient
from neo4j.exceptions import ServiceUnavailable

from core import persist_outcomes
from core.conversation_buffer import _conversation_buffers, clear_user_buffer
from core.erasure_guard import ErasureFenceChanged, erase_node, read_erasure_state
from core.memory_ops import MemoryOps
from core.task_registry import drain
from simple_chat_agent import SimpleChatAgent
from tests.support.recording_model import HashEmbedder, RecordingLLM, ZeroRanker

SECRET = "WALRUSX"
WARM = "WARMUPQ"
QUESTION = f"What do you remember about the vault code word {SECRET}?"
GRAPH_TS = "2026-01-01T00:00:00Z"


class Gate:
    def __init__(self):
        self.reached, self.release = asyncio.Event(), asyncio.Event()

    async def hit(self):
        self.reached.set()
        await self.release.wait()


class FlakyDriver:
    """Delegating driver that can be switched into a Neo4j outage."""

    def __init__(self, inner):
        self._inner, self.down = inner, False

    async def execute_query(self, *args, **kwargs):
        self.calls = getattr(self, "calls", 0) + 1
        if self.down:
            raise ServiceUnavailable("simulated Neo4j outage")
        return await self._inner.execute_query(*args, **kwargs)

    def clone(self, database):
        # Graphiti.add_episode does `self.driver = self.driver.clone(...)`; without this the
        # wrapper would be silently replaced by the real driver after the first write.
        return self

    def __getattr__(self, name):
        return getattr(self._inner, name)


class ChatProvider:
    def __init__(self):
        self.sent: list[list[dict]] = []
        self.gate: Gate | None = None
        self.on_call = None

    async def __call__(self, messages, context="chat", client=None):
        self.sent.append(copy.deepcopy(messages))
        if self.on_call:
            self.on_call()
        if self.gate:
            await self.gate.hit()
        return f"Reply: the code word is {SECRET}. (turn {len(self.sent)}) {WARM if len(self.sent) == 1 else ''}"


@pytest_asyncio.fixture
async def world(isolated_graph, neo4j_isolation, monkeypatch):
    monkeypatch.setattr("knowledge.ingest.get_embedding", AsyncMock(return_value=None))
    monkeypatch.setenv("FRACTAL_API_TOKEN", "scenario-c-token")
    monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    await isolated_graph.reset()
    s = neo4j_isolation
    graphiti = Graphiti(s.uri, s.user, s.password, llm_client=RecordingLLM(),
                        embedder=HashEmbedder(), cross_encoder=ZeroRanker())
    await graphiti.build_indices_and_constraints()
    flaky = FlakyDriver(graphiti.driver)
    graphiti.driver = flaky
    await flaky.execute_query(
        "CREATE (:Episodic {uuid:'secret-doc', group_id:'knowledge', name:'secret-doc', source:'text', "
        "content:$c, origin_class:'owner', source_description:'uploaded_text', entity_edges:[], valid_at:$ts, created_at:$ts})",
        c=f"The vault code word is {SECRET}. Keep it private.", ts=GRAPH_TS)
    await flaky.execute_query("CALL db.awaitIndexes(120)")
    provider = ChatProvider()
    monkeypatch.setattr("simple_chat_agent.llm_chat_response", provider)
    persist_outcomes.clear()
    clear_user_buffer("owner")
    agent = SimpleChatAgent(None, MemoryOps(graphiti, "owner"))

    async def dep():
        return graphiti

    monkeypatch.setattr("app.get_graphiti_dep", dep)

    class World:
        pass

    w = World()
    w.graphiti, w.flaky, w.provider, w.agent, w.monkeypatch = graphiti, flaky, provider, agent, monkeypatch
    try:
        yield w
    finally:
        await drain(timeout=10)
        clear_user_buffer("owner")
        persist_outcomes.clear()
        await graphiti.close()
        await isolated_graph.reset()


async def http_delete(uuid):
    from app import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/delete", json={"uuid": uuid, "hard": False},
                                     headers={"Authorization": "Bearer scenario-c-token"})
    assert response.status_code == 200 and response.json()["deleted"] is True


async def warmup(w):
    reply, _, _ = await w.agent.answer_core(f"Please remember the token {WARM} for later.")
    await drain(timeout=30)
    w.provider.sent.clear()
    w.persist_before = w.agent.last_persist_id
    return reply


async def chat_rows(w):
    rows = (await w.flaky._inner.execute_query(
        "MATCH (e:Episodic {source_description:'chat'}) RETURN e.episode_kind AS kind, "
        "coalesce(e.chat_finalized,false) AS finalized, coalesce(e.erasure_quarantined,false) AS quarantined")).records
    return [dict(r) for r in rows]


def ram_text():
    buf = _conversation_buffers.get("owner")
    return json.dumps(list(buf.buffer), default=str) if buf else ""


def has(text, marker):
    return marker in text


async def observe(w, label, reply, *, task_started_persist=True):
    await drain(timeout=30)
    sent = json.dumps(w.provider.sent, ensure_ascii=False)
    persist_id = w.agent.last_persist_id
    fresh = persist_id is not None and persist_id != getattr(w, "persist_before", None)
    outcome = persist_outcomes.get(persist_id, user_id="owner") if fresh else None
    rows = await chat_rows(w)
    return {
        "case": label,
        "provider_calls": len(w.provider.sent),
        "sent_to_provider": {"secret_in_recall_context": has(sent, SECRET) and "vault" in sent,
                             "ram_turn_in_request": has(sent, WARM)},
        "client_received": {"text": reply[:70], "has_model_answer": reply.startswith("Reply:")},
        "ram_after": {"has_secret": has(ram_text(), SECRET), "has_warmup_turn": has(ram_text(), WARM),
                      "turns": len(_conversation_buffers["owner"].buffer) // 2 if "owner" in _conversation_buffers else 0},
        "stored_chat_episodes": {"warmup_baseline": 1, "now": len(rows),
                                 "finalized": sum(r["finalized"] for r in rows),
                                 "quarantined": sum(r["quarantined"] for r in rows)},
        "published_status": (outcome or {}).get("status") if fresh else "no new persist id published",
    }


# --- 0. control: no deletion ----------------------------------------------------
@pytest.mark.asyncio
async def test_c0_control_no_deletion(world):
    await warmup(world)
    reply, _, _ = await world.agent.answer_core(QUESTION)
    obs = await observe(world, "no_delete", reply)
    print("OBSERVED_C " + json.dumps(obs, sort_keys=True))
    assert obs["sent_to_provider"] == {"secret_in_recall_context": True, "ram_turn_in_request": True}
    w_status = obs["published_status"]
    assert obs["client_received"]["has_model_answer"] and w_status == "persisted"
    assert obs["stored_chat_episodes"]["now"] == 2 and obs["stored_chat_episodes"]["finalized"] == 2


# --- 1. delete before the context is sent -----------------------------------------
@pytest.mark.asyncio
async def test_c1_delete_before_context_is_sent(world):
    await warmup(world)
    original = MemoryOps.build_context_for_query
    gate = Gate()

    async def gated(self, *a, **k):
        ctx = await original(self, *a, **k)      # context (with the secret) already built
        await gate.hit()
        return ctx

    world.monkeypatch.setattr(MemoryOps, "build_context_for_query", gated)
    task = asyncio.create_task(world.agent.answer_core(QUESTION))
    await gate.reached.wait()
    await http_delete("secret-doc")
    gate.release.set()
    reply, _, _ = await task
    obs = await observe(world, "delete_before_send", reply)
    print("OBSERVED_C " + json.dumps(obs, sort_keys=True))
    assert obs["provider_calls"] == 0 and not obs["client_received"]["has_model_answer"]
    assert obs["ram_after"]["has_secret"] is False and obs["stored_chat_episodes"]["now"] == 1


# --- 2. delete while the model is answering ---------------------------------------
@pytest.mark.asyncio
async def test_c2_delete_during_model_wait(world):
    await warmup(world)
    world.provider.gate = Gate()
    task = asyncio.create_task(world.agent.answer_core(QUESTION))
    await world.provider.gate.reached.wait()
    await http_delete("secret-doc")
    world.provider.gate.release.set()
    reply, _, _ = await task
    obs = await observe(world, "delete_during_model", reply)
    print("OBSERVED_C " + json.dumps(obs, sort_keys=True))
    assert obs["provider_calls"] == 1 and obs["sent_to_provider"]["secret_in_recall_context"] is True  # already sent
    assert not obs["client_received"]["has_model_answer"]
    assert obs["ram_after"]["has_secret"] is False and obs["stored_chat_episodes"]["now"] == 1


# --- 3. delete after generation, before the background save -----------------------
@pytest.mark.asyncio
async def test_c3_delete_after_generation_before_background_save(world):
    await warmup(world)
    import simple_chat_agent
    real_spawn, gate, tasks = simple_chat_agent.spawn, Gate(), []

    def gated_spawn(coro, name=None):
        async def run():
            await gate.release.wait()
            return await coro
        task = real_spawn(run(), name=name)
        tasks.append(task)
        return task

    world.monkeypatch.setattr("simple_chat_agent.spawn", gated_spawn)
    reply, _, _ = await world.agent.answer_core(QUESTION)       # generation done, save not started
    before_ram = ram_text()
    await http_delete("secret-doc")
    gate.release.set()
    await asyncio.gather(*tasks)
    obs = await observe(world, "delete_after_generation", reply)
    obs["ram_before_delete_had_answer"] = has(before_ram, SECRET)
    print("OBSERVED_C " + json.dumps(obs, sort_keys=True))
    assert obs["client_received"]["has_model_answer"]            # delivered before the delete
    assert obs["stored_chat_episodes"]["now"] == 1               # the answer was not stored
    assert obs["published_status"] in {"unconfirmed", "failed"}


# --- 4. Neo4j unavailable at the revision checks -----------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["at_start", "before_send", "after_generation"])
async def test_c4_neo4j_unavailable(world, phase):
    await warmup(world)
    if phase == "at_start":
        world.flaky.down = True
    elif phase == "before_send":
        original, gate = MemoryOps.build_context_for_query, None

        async def outage_after_recall(self, *a, **k):
            ctx = await original(self, *a, **k)
            world.flaky.down = True
            return ctx

        world.monkeypatch.setattr(MemoryOps, "build_context_for_query", outage_after_recall)
    else:
        world.provider.on_call = lambda: setattr(world.flaky, "down", True)
    reply, _, _ = await world.agent.answer_core(QUESTION)
    world.flaky.down = False
    obs = await observe(world, f"neo4j_down_{phase}", reply)
    print("OBSERVED_C " + json.dumps(obs, sort_keys=True))
    assert not obs["client_received"]["has_model_answer"]       # fail closed in every phase
    assert obs["stored_chat_episodes"]["now"] == 1
    if phase in {"at_start", "before_send"}:
        assert obs["provider_calls"] == 0
    else:
        assert obs["provider_calls"] == 1


# --- 5. finalize_chat_episode on real Neo4j ------------------------------------------
TURN_KEYS = dict(episode_kind="chat_turn", is_correction=False, summarized=False, recall_derived=False,
                 recall_context_sha256=None, recall_query_sha256=None, recall_source_ids=[],
                 recall_guard_authoritative=False)


def _turn_meta(i):
    return {**TURN_KEYS, "conversation_id": "conv-f", "turn_index": i}


def _summary_meta(sources):
    return {"episode_kind": "chat_summary", "conversation_id": "conv-f", "covers_turns": "1-10",
            "summarized_turns": sources, "provenance_id": "p1", "provenance_activity": "chat_summary_synthesis",
            "provenance_agent": "fractal:summary", "payload_sha256": "0" * 64, "derived_source_ids": sources}


async def _raw_episode(w, uuid):
    await w.flaky._inner.execute_query(
        "CREATE (:Episodic {uuid:$u, group_id:'personal', name:'chat', source:'text', source_description:'chat', "
        "content:'x', entity_edges:[], valid_at:$ts, created_at:$ts})", u=uuid, ts=GRAPH_TS)


async def _finalized_turns(w, state):
    from core.chat_persistence import finalize_chat_episode
    uuids = []
    for i in range(1, 11):
        uuids.append(f"turn-{i}")
        await _raw_episode(w, f"turn-{i}")
        await finalize_chat_episode(w.graphiti, episode_uuid=f"turn-{i}", user_id="owner", group_id="personal",
                                    expected_state=state, metadata=_turn_meta(i))
    return uuids


async def _fields(w, uuid):
    r = (await w.flaky._inner.execute_query(
        "MATCH (e:Episodic {uuid:$u}) RETURN e.chat_finalized AS fin, e.origin_class AS origin, "
        "e.summarized AS summarized, e.summary_uuid AS summary_uuid, "
        "EXISTS { MATCH (e)-[:STORED_FOR]->(:User {user_id:'owner'}) } AS stored_for, "
        "EXISTS { MATCH (:User)-[:AUTHORED]->(e) } AS authored", u=uuid)).records
    return dict(r[0])


@pytest.mark.asyncio
async def test_c5_finalize_normal_turn_and_valid_summary(world):
    from core.chat_persistence import finalize_chat_episode
    state = await read_erasure_state(world.graphiti)
    sources = await _finalized_turns(world, state)
    turn = await _fields(world, "turn-1")
    assert turn["fin"] is True and turn["origin"] == "agent_derived"
    assert turn["stored_for"] is True and turn["authored"] is False
    await _raw_episode(world, "summary-ok")
    await finalize_chat_episode(world.graphiti, episode_uuid="summary-ok", user_id="owner", group_id="personal",
                                expected_state=state, metadata=_summary_meta(sources))
    assert (await _fields(world, "summary-ok"))["fin"] is True
    marked = [await _fields(world, u) for u in sources]
    assert all(m["summarized"] is True and m["summary_uuid"] == "summary-ok" for m in marked)
    with pytest.raises(ErasureFenceChanged):     # a finalized episode cannot be finalized twice
        await finalize_chat_episode(world.graphiti, episode_uuid="turn-1", user_id="owner", group_id="personal",
                                    expected_state=state, metadata=_turn_meta(1))


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["source_erased_revision_bumped", "source_quarantined_same_revision",
                                 "source_in_open_erasure_area"])
async def test_c5_summary_with_a_dropped_source_is_denied_and_changes_nothing(world, how):
    from core.chat_persistence import finalize_chat_episode
    state = await read_erasure_state(world.graphiti)
    sources = await _finalized_turns(world, state)
    await _raw_episode(world, "summary-bad")
    expected = state
    if how == "source_erased_revision_bumped":
        assert (await erase_node(world.graphiti, uuid="turn-3"))["deleted"]
    elif how == "source_quarantined_same_revision":
        await world.flaky._inner.execute_query("MATCH (e:Episodic {uuid:'turn-3'}) SET e.erasure_quarantined=true")
    else:   # variant B: a source marked by an open erasure (e.g. recalled the erased text) is not admitted
        await world.flaky._inner.execute_query(
            "MATCH (e:Episodic {uuid:'turn-3'}) SET e.erasure_quarantine=['evt:derived']")
    with pytest.raises(ErasureFenceChanged):
        await finalize_chat_episode(world.graphiti, episode_uuid="summary-bad", user_id="owner", group_id="personal",
                                    expected_state=expected, metadata=_summary_meta(sources))
    bad = await _fields(world, "summary-bad")
    assert bad["fin"] is None and bad["stored_for"] is False            # nothing certified
    others = [await _fields(world, u) for u in sources if u != "turn-3"]
    assert all(o["summarized"] is False for o in others)                # sources not marked


# --- 6. delete performed by ANOTHER process: the local RAM buffer is not cleared by it ---
@pytest.mark.asyncio
async def test_c6_delete_by_another_process_does_not_leave_stale_ram(world):
    await warmup(world)
    world.provider.gate = Gate()
    task = asyncio.create_task(world.agent.answer_core(QUESTION))
    await world.provider.gate.reached.wait()
    assert (await erase_node(world.graphiti, uuid="secret-doc"))["deleted"]   # no buffer clearing here
    assert WARM in ram_text()                                                  # RAM is stale at this moment
    world.provider.gate.release.set()
    reply, _, _ = await task
    first = await observe(world, "other_process_delete_during_model", reply)
    world.provider.gate = None
    world.provider.sent.clear()
    reply2, _, _ = await world.agent.answer_core("Tell me anything you still know, please.")
    second_request = json.dumps(world.provider.sent, ensure_ascii=False)
    obs = {**first, "next_turn": {"model_answered": reply2.startswith("Reply:"),
                                  "request_has_old_ram_turn": WARM in second_request,
                                  "request_has_deleted_secret": "vault" in second_request}}
    print("OBSERVED_C " + json.dumps(obs, sort_keys=True))
    assert not first["client_received"]["has_model_answer"] and first["ram_after"]["turns"] == 0
    assert obs["next_turn"]["request_has_old_ram_turn"] is False and obs["next_turn"]["request_has_deleted_secret"] is False
