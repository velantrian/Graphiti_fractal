"""Item B: an ordinary chat answer while an unknown-outcome write holds a group.

State: text A (group knowledge) was erased; a write that started BEFORE the erasure kept running,
stored derived objects that restate A (the recording model puts into an entity summary every
prompt sentence that mentions the entity, and A was in its previous-episode window), and then its
reply was lost. Its objects are unknown, hence unmarked; the revision is stable afterwards.

Observed three things separately, with a recording client in place of the answering model:
  1. what ordinary search returns,
  2. the exact messages the answering model receives (recall must really have run: positive control),
  3. the outcome of the background persistence of the turn.
Real: graphiti_core 0.29.3 add_episode, ingest, erase_node, MemoryOps recall, SimpleChatAgent.answer_core
and POST /chat, the admission Cypher. Simulated: both models (recording stubs).
"""
import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from graphiti_core import Graphiti
from graphiti_core.llm_client.errors import RateLimitError
from httpx import ASGITransport, AsyncClient

from core import persist_outcomes, settlement
from core.erasure_guard import erase_node
from core.late_writes import list_unsettled_writes
from core.memory_ops import MemoryOps
from core.settlement import settle_unsettled_write
from core.task_registry import drain
from simple_chat_agent import SimpleChatAgent
from tests.integration.test_variant_b_vertical import Env
from tests.support.recording_model import HashEmbedder, ParaphrasingLLM, ZeroRanker

K, P = "knowledge", "project"
MARK = "ZORBLAX-ALPHA"
LEAK = "strongbox"                       # the paraphrase the model writes when erased text is in its input
CONTROL = "JELLYFISH"
A_TEXT = f"[[Mira Venn]] keeps the Vault passphrase {MARK} in the [[Amber Cabinet]]."
C_TEXT = "[[Vault]] was inspected by [[Courier Zed]] today, ref NEWQ9."
CONTROL_TEXT = f"[[Cy Ward]] walks the dog {CONTROL} at noon near the [[Harbor Office]]."
QUESTION = f"What do we know about the Vault passphrase and Courier Zed, and Cy Ward {CONTROL} Harbor Office?"
TOKENS = re.compile(rf"{MARK}|{LEAK}|{CONTROL}|NEWQ9")


class RecordingChat:
    """Stands in for the answering model's client: keeps every message it is given."""

    def __init__(self):
        self.calls: list[list[dict]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, model, messages, **_):
        self.calls.append(messages)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Noted."))])

    def text(self) -> str:
        return "\n".join(str(m.get("content", "")) for call in self.calls for m in call)


@pytest_asyncio.fixture
async def env(isolated_graph, neo4j_isolation, monkeypatch):
    monkeypatch.setattr("knowledge.ingest.get_embedding", AsyncMock(return_value=None))
    monkeypatch.setattr(settlement, "SETTLEMENT_MIN_AGE_SECONDS", 0)
    monkeypatch.setenv("FRACTAL_API_TOKEN", "chat-token")
    monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    await isolated_graph.reset()
    s, llm = neo4j_isolation, ParaphrasingLLM()
    graphiti = Graphiti(s.uri, s.user, s.password, llm_client=llm, embedder=HashEmbedder(), cross_encoder=ZeroRanker())
    await graphiti.build_indices_and_constraints()
    e = Env(graphiti, llm, monkeypatch)
    persist_outcomes.clear()
    try:
        yield e
    finally:
        await graphiti.close()
        await isolated_graph.reset()


async def open_ambiguous_attempt(env):
    """Erase A while a write that already read it is running, then lose that write's reply."""
    await env.add(A_TEXT, K)
    await env.add(CONTROL_TEXT, P)
    a_uuid = await env.uuid_of(MARK)
    real_add = env.graphiti.add_episode

    async def lossy(**kwargs):
        await real_add(**kwargs)                                  # committed ...
        raise RateLimitError("Please try again in 0.01s.")        # ... but the caller never learns

    env.monkeypatch.setattr(env.graphiti, "add_episode", lossy)
    gate = env.llm.hold_next("extract_nodes.extract_message")
    env.llm.expect(C_TEXT)
    task = asyncio.create_task(env.add(C_TEXT, K))
    await asyncio.wait_for(gate.reached.wait(), 30)
    await erase_node(env.graphiti, uuid=a_uuid)                    # the erasure lands while the write runs
    gate.release.set()
    result = await task
    env.monkeypatch.setattr(env.graphiti, "add_episode", real_add)
    return a_uuid, result


async def vault_row(env):
    rows = await env.q("MATCH (n:Entity {name:'Vault', group_id:$g}) RETURN n.summary AS s, "
                       "size(coalesce(n.erasure_quarantine,[])) AS marks", g=K)
    assert len(rows) == 1
    return rows[0]


async def ask(env, message=QUESTION):
    client = RecordingChat()
    agent = SimpleChatAgent(client, MemoryOps(env.graphiti, "owner"))
    env.llm.expect("")                                              # the background turn has no entities
    before_calls = len(env.llm.calls)
    reply, _, context = await agent.answer_core(message)
    await drain(timeout=60)
    return SimpleNamespace(client=client, reply=reply, context=context, agent=agent,
                           persist=persist_outcomes.get(agent.last_persist_id, user_id="owner"),
                           bg_model_calls=len(env.llm.calls) - before_calls)


@pytest.mark.asyncio
async def test_precondition_the_unknown_write_stored_erased_text_in_an_unmarked_object(env):
    a_uuid, result = await open_ambiguous_attempt(env)
    assert result["completeness"] == "unconfirmed" and result["unconfirmed_writes"] == 1
    edge = await env.q("MATCH ()-[r:RELATES_TO]->() WHERE r.fact CONTAINS $x RETURN count(r) AS c, "
                       "sum(size(coalesce(r.erasure_quarantine,[]))) AS marks", x=LEAK)
    assert edge[0]["c"] == 1 and edge[0]["marks"] == 0           # observed: restated in other words, and nothing marks it
    pending = await list_unsettled_writes(env.graphiti)
    assert [(p["kind"], p["group_id"], p["episode_uuid"]) for p in pending] == [("ingest", K, None)]


@pytest.mark.asyncio
async def test_answer_core_does_not_send_text_of_an_unknown_write_to_the_answering_model(env):
    await open_ambiguous_attempt(env)
    memory = MemoryOps(env.graphiti, "owner")
    searched = await memory.search_memory(QUESTION, scopes=["personal", P, K, "experience"], limit=50)
    search_tokens = set(TOKEN_FINDALL(searched))
    out = await ask(env)
    sent = out.client.text()
    observed = {
        "search_tokens": sorted(search_tokens), "model_calls": len(out.client.calls),
        "recall_status": out.context.receipt.status if out.context and out.context.receipt else None,
        "control_in_model_input": CONTROL in sent, "erased_in_model_input": MARK in sent,
        "paraphrase_in_model_input": LEAK in sent,
        "persist": out.persist and out.persist.get("status"),
    }
    print("OBSERVED_B " + json.dumps(observed, sort_keys=True))
    # positive control: recall ran and delivered an independent fact, in search and in the model input
    assert out.context is not None and out.context.receipt.status == "OK"
    assert CONTROL in search_tokens and CONTROL in sent
    # the potentially affected text must not reach the answering model (nor ordinary search)
    assert MARK not in sent and LEAK not in sent, "text restated by an unknown write reached the answering model"
    assert MARK not in search_tokens and LEAK not in search_tokens
    assert len(out.client.calls) == 1 and out.reply == "Noted."


@pytest.mark.asyncio
async def test_background_persistence_of_the_turn_is_reported_not_silent(env):
    await open_ambiguous_attempt(env)
    out = await ask(env)
    # the turn is saved in the personal group, which the unknown write does not hold: it must say so truthfully
    assert out.persist is not None and out.persist["status"] in ("persisted", "unconfirmed", "partial", "unknown", "failed")
    rows = await env.q("MATCH (e:Episodic {group_id:'personal', source_description:'chat'}) RETURN count(e) AS c")
    assert (out.persist["status"] == "persisted") == (rows[0]["c"] == 1)


@pytest.mark.asyncio
async def test_post_chat_gets_the_same_protection(env, monkeypatch):
    from app import app
    await open_ambiguous_attempt(env)
    client = RecordingChat()

    async def dep():
        return env.graphiti

    monkeypatch.setattr("app.get_graphiti_dep", dep)
    monkeypatch.setattr("core.llm.get_async_client", lambda: client)
    env.llm.expect("")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.post("/chat", json={"message": QUESTION}, headers={"Authorization": "Bearer chat-token"})
    await drain(timeout=60)
    assert response.status_code == 200
    assert CONTROL in client.text() and LEAK not in client.text() and MARK not in client.text()


@pytest.mark.asyncio
async def test_settlement_gives_the_group_back_to_recall_without_the_erased_text(env):
    a_uuid, _ = await open_ambiguous_attempt(env)
    pending = (await list_unsettled_writes(env.graphiti))[0]
    landed = (await env.q("MATCH (e:Episodic {group_id:$g}) WHERE e.content CONTAINS 'NEWQ9' RETURN e.uuid AS u", g=K))[0]["u"]
    done = await settle_unsettled_write(env.graphiti, kind="ingest", key=pending["key"], group_id=K,
                                        outcome="contained", decided_by="owner:ruslan", episode_uuids=[landed])
    assert done["settled"]
    out = await ask(env)
    assert CONTROL in out.client.text() and LEAK not in out.client.text()


def TOKEN_FINDALL(searched) -> list[str]:
    items = [*searched.episodes, *searched.entities, *searched.edges, *searched.communities]
    return TOKENS.findall(json.dumps(items, default=str))
