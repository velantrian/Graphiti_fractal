"""Ambiguous late writes (case 1 of the final review of #58) on a disposable Neo4j.

A write reaches Graphiti, an erasure lands while it runs, and the reply is lost (error after the
commit, timeout, process death). Which objects changed is unknown, so the group must not resume
model use automatically; what is known is contained; the rest needs an explicit, evidence-based
settlement. Everything below the model is real: claims, chat attempts, erase_node, the rebuild,
admission queries, a "restarted process" (a second, independent connection and client object).
Only Graphiti.add_episode is replaced by the recording stub (tests/support/graphiti_stub.py) - except
in the first test, which runs the real graphiti_core 0.29.3 add_episode to pin its uuid semantics.
"""
import asyncio
from importlib.metadata import version
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from graphiti_core import Graphiti
from graphiti_core.errors import NodeNotFoundError
from graphiti_core.llm_client.errors import RateLimitError

from core.erasure_guard import (
    GroupWriteRestricted, erase_node, read_erasure_state, require_group_writable, visible_node,
)
from core.erasure_scope import rebuild_area, scope_pending_erasures
from core.ingest_atomicity import acquire_ingest_claim, mark_ingest_claim_episode_created, mark_ingest_claim_write_attempt
from core.late_writes import list_unsettled_writes, settle_unsettled_write
from core.text_utils import fingerprint
from knowledge.ingest import ingest_text_document
from tests.integration.conftest import IsolatedGraph
from tests.support.graphiti_stub import StubDriver, StubGraphiti, write_then_raise
from tests.support.neo4j_isolation import open_admitted_async_driver
from tests.support.recording_model import HashEmbedder, RecordingLLM, ZeroRanker

G, H = "amb-g", "amb-h"


def text(label):
    return f"Synthetic ambiguous write fact {label}: the key to room {label} hangs on hook {label}. " * 3


@pytest_asyncio.fixture
async def world(isolated_graph, neo4j_isolation, monkeypatch):
    """stub = the process that writes; restarted = a second process on its own connection."""
    monkeypatch.setattr("knowledge.ingest.get_embedding", AsyncMock(return_value=None))
    driver2 = await open_admitted_async_driver(neo4j_isolation)
    stub = StubGraphiti(StubDriver(isolated_graph._raw, neo4j_isolation.database))
    restarted = StubGraphiti(StubDriver(driver2, neo4j_isolation.database))
    try:
        yield SimpleNamespace(stub=stub, restarted=restarted, graph=isolated_graph)
    finally:
        await driver2.close()


async def q(graph, cypher, **p):
    return [dict(r) for r in (await graph.driver.execute_query(cypher, **p)).records]


async def seed_old(g, uuid="old-1", group=G):
    await q(g, "CREATE (:Episodic {uuid:$u, group_id:$g, content:'older text', name:'old', "
               "source_description:'seed', created_at:datetime(), valid_at:datetime()})", u=uuid, g=group)


def erasing_write(target, *, then):
    """add_episode that stores its episode, an erasure lands during the call, then the reply is lost."""
    async def step(stub, kwargs):
        await stub.write(kwargs)
        await erase_node(stub, uuid=target)
        raise then
    return step


async def tomb(g, uuid):
    return (await q(g, "MATCH (t:FractalErasure {target_uuid:$u}) RETURN properties(t) AS t", u=uuid))[0]["t"]


async def refused(g, group):
    try:
        await require_group_writable(g, group)
        return None
    except GroupWriteRestricted as exc:
        return str(exc)


# --- 1. what graphiti_core 0.29.3 does with add_episode(uuid=...) ----------------------------------

@pytest.mark.asyncio
async def test_graphiti_0_29_3_add_episode_uuid_is_a_reprocess_handle_not_an_idempotency_key(isolated_graph, neo4j_isolation):
    """Observed (live): this is why a lost reply cannot be repaired by calling add_episode again."""
    assert version("graphiti-core") == "0.29.3"
    from datetime import datetime, timezone
    s, llm = neo4j_isolation, RecordingLLM()
    g = Graphiti(s.uri, s.user, s.password, llm_client=llm, embedder=HashEmbedder(), cross_encoder=ZeroRanker())
    try:
        await g.build_indices_and_constraints()

        async def counts():
            row = (await q(g, "CALL () { MATCH (e:Episodic) RETURN count(e) AS ep } "
                              "CALL () { MATCH (n:Entity) RETURN count(n) AS en } "
                              "CALL () { MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS ed } RETURN ep, en, ed"))[0]
            return row["ep"], row["en"], row["ed"]

        async def add(body, **kw):
            llm.expect(body)
            return await g.add_episode(name="n", episode_body=body, source_description="t",
                                       reference_time=datetime.now(timezone.utc), group_id=G, **kw)

        body = "[[Mira Venn]] keeps the key in the [[Amber Cabinet]]."
        first = await add(body)
        assert await counts() == (1, 2, 1)
        calls = len(llm.calls)

        with pytest.raises(NodeNotFoundError):                 # unknown uuid: refused, nothing written, no model call
            await add("[[Other]] and [[Thing]] differ.", uuid="never-created")
        assert await counts() == (1, 2, 1) and len(llm.calls) == calls

        again = await add("[[Other]] and [[Thing]] differ.", uuid=first.episode.uuid)
        assert again.episode.uuid == first.episode.uuid         # same node ...
        assert (await counts())[0] == 1                         # ... no second episode ...
        assert (await counts())[1:] == (4, 2)                   # ... but derived objects were created AGAIN
        assert len(llm.calls) > calls                           # ... by new model calls ...
        stored = (await q(g, "MATCH (e:Episodic {uuid:$u}) RETURN e.content AS c", u=first.episode.uuid))[0]["c"]
        assert stored == body                                   # ... while the stored content stayed the original
    finally:
        await g.close()


# --- 2. ingest: the reply is lost after the write, an erasure landed meanwhile ---------------------

@pytest.mark.asyncio
async def test_ingest_lost_reply_across_an_erasure_pauses_the_group_until_settled(world):
    stub, other, g = world.stub, world.restarted, world.graph
    await seed_old(g)
    body = text("a")
    stub.plan = [erasing_write("old-1", then=RateLimitError("Please try again in 0.01s."))]
    result = await ingest_text_document(stub, body, group_id=G, user_id="owner")
    assert result["completeness"] == "unconfirmed" and result["unconfirmed_writes"] == 1
    landed = (await q(g, "MATCH (e:Episodic {group_id:$g}) WHERE e.content=$c RETURN e.uuid AS u", g=G, c=body.strip()))
    assert len(landed) == 1                                     # it DID write; the caller never learned the uuid
    claim = (await q(g, "MATCH (c:FractalIngestClaim {group_id:$g}) RETURN c.state AS s, c.episode_uuid AS e, "
                        "c.late_settled_at AS l", g=G))[0]
    assert claim == {"s": "RECONCILIATION_REQUIRED", "e": None, "l": None}

    t = await tomb(g, "old-1")                                  # the erasure's event is held open for this group
    assert t["status"] == "SCOPED" and G in t["blocked_groups"] and t["held_for_unsettled_write"] is True
    assert "outcome is unknown" in (await refused(other, G))    # a restarted process sees the same pause
    calls = len(stub.calls)
    again = await ingest_text_document(stub, text("b"), group_id=G, user_id="owner")
    assert len(stub.calls) == calls and again["completeness"] != "complete"      # the model was not used
    assert await refused(other, H) is None                      # an independent group is not paused
    free = await ingest_text_document(stub, text("h"), group_id=H, user_id="owner")
    assert free["completeness"] == "complete" and len(stub.calls) == calls + 1

    await rebuild_area(g, t["event_id"])                        # a rebuild alone must not resume the group
    assert (await tomb(g, "old-1"))["status"] == "SCOPED" and await refused(other, G)

    pending = await list_unsettled_writes(other)
    assert [(p["kind"], p["group_id"], p["episode_uuid"]) for p in pending] == [("ingest", G, None)]
    with pytest.raises(ValueError):                             # 'contained' must name what landed
        await settle_unsettled_write(other, kind="ingest", key=pending[0]["key"], outcome="contained")
    assert (await settle_unsettled_write(other, kind="ingest", key="nope", outcome="no_write"))["settled"] is False

    done = await settle_unsettled_write(other, kind="ingest", key=pending[0]["key"], outcome="contained",
                                        episode_uuids=[landed[0]["u"]])
    assert done["settled"] and done["events_still_open"] == []
    assert (await tomb(g, "old-1"))["status"] == "RESOLVED" and await refused(other, G) is None
    hidden = await q(g, f"MATCH (e:Episodic {{uuid:$u}}) WHERE {visible_node('e')} RETURN count(e) AS c", u=landed[0]["u"])
    assert hidden[0]["c"] == 0, "the episode that landed unconfirmed stays out of retrieval"


@pytest.mark.asyncio
async def test_ingest_settled_as_no_write_after_verification_resumes_the_group(world):
    stub, other, g = world.stub, world.restarted, world.graph
    await seed_old(g)
    stub.plan = [erasing_write("old-1", then=TimeoutError("socket closed"))]
    await ingest_text_document(stub, text("a"), group_id=G, user_id="owner")
    assert await refused(other, G)
    key = (await list_unsettled_writes(other))[0]["key"]
    done = await settle_unsettled_write(other, kind="ingest", key=key, outcome="no_write")
    assert done["events_still_open"] == [] and await refused(other, G) is None
    assert (await q(g, "MATCH (c:FractalIngestClaim) RETURN c.late_settlement AS s"))[0]["s"] == "no_write"


@pytest.mark.asyncio
async def test_an_erasure_in_another_group_does_not_pause_an_unconfirmed_write(world):
    stub, other, g = world.stub, world.restarted, world.graph
    await seed_old(g, "old-h", group=H)
    stub.plan = [erasing_write("old-h", then=RateLimitError("Please try again in 0.01s."))]
    await ingest_text_document(stub, text("a"), group_id=G, user_id="owner")     # unconfirmed in G, erasure in H
    assert await refused(other, G) is None and "outcome is unknown" in (await refused(other, H) or "outcome is unknown")


# --- 3. the process dies: nothing in memory, everything from durable records --------------------------

@pytest.mark.asyncio
async def test_process_death_after_the_write_attempt_keeps_the_group_paused_after_restart(world):
    stub, other, g = world.stub, world.restarted, world.graph
    await seed_old(g)
    body = text("dead")
    token = await acquire_ingest_claim(stub, group_id=G, fingerprint=fingerprint(body.strip()), expected_revision=0)
    await mark_ingest_claim_write_attempt(stub, group_id=G, fingerprint=fingerprint(body.strip()), token=token)
    # ... add_episode reaches Graphiti, the process dies: no handler ever runs. An erasure follows.
    ev = (await erase_node(other, uuid="old-1"))["event_id"]
    assert "outcome is unknown" in (await refused(other, G))    # derived from the claim alone
    await scope_pending_erasures(other)                         # the recovery sweep, run by the new process
    t = await tomb(g, "old-1")
    assert t["status"] == "SCOPED" and G in t["blocked_groups"]
    await rebuild_area(other, ev)
    assert (await tomb(g, "old-1"))["status"] == "SCOPED" and await refused(other, G)


@pytest.mark.asyncio
async def test_process_death_after_the_episode_is_known_is_contained_automatically_on_recovery(world):
    stub, other, g = world.stub, world.restarted, world.graph
    await seed_old(g)
    body = text("known")
    fp = fingerprint(body.strip())
    token = await acquire_ingest_claim(stub, group_id=G, fingerprint=fp, expected_revision=0)
    await mark_ingest_claim_write_attempt(stub, group_id=G, fingerprint=fp, token=token)
    landed = (await stub.write({"name": "n", "episode_body": body.strip(), "source_description": "s", "group_id": G}))
    await mark_ingest_claim_episode_created(stub, group_id=G, fingerprint=fp, token=token,
                                            episode_uuid=landed.episode.uuid)
    ev = (await erase_node(other, uuid="old-1"))["event_id"]    # the process died before containing it
    assert await refused(other, G)
    await scope_pending_erasures(other)                         # sweep: the uuid is known -> contained + settled
    assert (await q(g, "MATCH (c:FractalIngestClaim) RETURN c.late_settlement AS s"))[0]["s"] == "contained"
    await rebuild_area(other, ev)
    assert (await tomb(g, "old-1"))["status"] == "RESOLVED" and await refused(other, G) is None
    vis = await q(g, f"MATCH (e:Episodic {{uuid:$u}}) WHERE {visible_node('e')} RETURN count(e) AS c", u=landed.episode.uuid)
    assert vis[0]["c"] == 0


# --- 4. chat path ----------------------------------------------------------------------------------------

def chat_agent(stub):
    from simple_chat_agent import SimpleChatAgent
    agent = SimpleChatAgent.__new__(SimpleChatAgent)
    agent.last_persist_id, agent.llm_client = None, None
    agent.memory = SimpleNamespace(graphiti=stub, user_id="owner")
    return agent


async def chat_group():
    from core.config import get_config
    return get_config().memory.personal_group_id


@pytest.mark.asyncio
async def test_chat_write_that_completes_settles_its_attempt(world):
    stub, g = world.stub, world.graph
    agent = chat_agent(stub)
    state = await read_erasure_state(stub)
    out = await agent._persist_turn_pipeline(conversation_id="c1", conversation_text="User: hello\nAssistant: hi",
                                             expected_state=state)
    assert out["status"] == "persisted", out
    rows = await q(g, "MATCH (a:FractalChatWriteAttempt) RETURN a.state AS s, a.settlement AS m, a.episode_uuid AS u")
    assert len(rows) == 1 and rows[0]["s"] == "SETTLED" and rows[0]["m"] == "completed" and rows[0]["u"]


@pytest.mark.asyncio
async def test_chat_lost_reply_across_an_erasure_pauses_chat_until_settled(world):
    stub, other, g = world.stub, world.restarted, world.graph
    await seed_old(g)                                           # erased in group G; chat lives in the personal group
    personal = await chat_group()
    agent = chat_agent(stub)
    state = await read_erasure_state(stub)
    stub.plan = [erasing_write("old-1", then=TimeoutError("reply lost"))]
    out = await agent._persist_turn_pipeline(conversation_id="c2", conversation_text="User: secret\nAssistant: ok",
                                             expected_state=state)
    assert out["status"] in ("unknown", "partial"), out         # never "persisted"
    landed = await q(g, "MATCH (e:Episodic {group_id:$g}) WHERE e.source_description='chat' RETURN e.uuid AS u", g=personal)
    assert len(landed) == 1                                     # the turn landed; the caller never learned its uuid

    t = await tomb(g, "old-1")
    assert t["status"] == "SCOPED" and personal in t["blocked_groups"]
    assert "outcome is unknown" in (await refused(other, personal))
    state2 = await read_erasure_state(other)
    calls = len(stub.calls)
    out2 = await chat_agent(stub)._persist_turn_pipeline(conversation_id="c2", conversation_text="User: next",
                                                         expected_state=state2)
    assert out2["status"] != "persisted" and len(stub.calls) == calls          # next chat write: model not used
    assert await refused(other, H) is None

    pending = await list_unsettled_writes(other)
    assert [(p["kind"], p["group_id"]) for p in pending] == [("chat", personal)]
    done = await settle_unsettled_write(other, kind="chat", key=pending[0]["key"], outcome="contained",
                                        episode_uuids=[landed[0]["u"]])
    assert done["settled"] and await refused(other, personal) is None
    assert (await tomb(g, "old-1"))["status"] == "RESOLVED"


@pytest.mark.asyncio
async def test_chat_attempt_is_not_created_when_a_check_fails_before_the_call(world):
    stub, g = world.stub, world.graph
    state = await read_erasure_state(stub)
    await seed_old(g)
    await erase_node(stub, uuid="old-1")                         # revision moved after `state` was read
    out = await chat_agent(stub)._persist_turn_pipeline(conversation_id="c3", conversation_text="User: x",
                                                        expected_state=state)
    assert out["status"] != "persisted" and stub.calls == []
    assert await q(g, "MATCH (a:FractalChatWriteAttempt) RETURN a") == []
