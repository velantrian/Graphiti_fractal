"""Shared scenario for the MCP read->write lineage tests (real Graphiti add_episode, recording model stubs).

Data
  S   source episode in group knowledge (owner): holds SRC
  C   independent control fact in group project (owner): JELLYFISH
  D   retelling of S written through MCP memory.remember into group project: holds RET; its graph objects are
      Entity "Mira Venn" (summary with RET) and an edge (fact with RET) in group project
Observations are always taken three ways: MemoryOps.search_memory over all four scopes, MCP memory.search_knowledge,
and the exact messages SimpleChatAgent.answer_core sends to the (recording) answering model.
"""
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest_asyncio
from graphiti_core import Graphiti

from core import persist_outcomes, settlement
from core.erasure_guard import erase_node
from core.memory_ops import MemoryOps
from core.task_registry import drain
from mcp_server import server
from simple_chat_agent import SimpleChatAgent
from tests.integration.test_chat_answer_gate import RecordingChat
from tests.integration.test_variant_b_vertical import Env
from tests.support.recording_model import HashEmbedder, RecordingLLM, ZeroRanker

K, P = "knowledge", "project"
SRC, RET, CONTROL = "ZORBLAX-ALPHA", "RETOLD-7", "JELLYFISH"
S_TEXT = f"[[Mira Venn]] keeps the vault passphrase {SRC} in the [[Amber Cabinet]]."
D_TEXT = f"[[Mira Venn]] guards a secret phrase {RET} in the [[Amber Cabinet]]."
C_TEXT = f"[[Cy Ward]] walks the dog {CONTROL} at noon near the [[Harbor Office]]."
# no marker token in the query: it is part of the user message the answering model receives
QUERY = "vault passphrase secret phrase guards keeps Mira Venn Amber Cabinet Cy Ward walks dog noon Harbor Office"
TOKENS = re.compile(rf"{SRC}|{RET}|{CONTROL}")


@pytest_asyncio.fixture
async def env(isolated_graph, neo4j_isolation, monkeypatch):
    monkeypatch.setattr("knowledge.ingest.get_embedding", AsyncMock(return_value=None))
    monkeypatch.setattr(settlement, "SETTLEMENT_MIN_AGE_SECONDS", 0)
    monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    monkeypatch.setenv("FRACTAL_RECALL_TIMEOUT_SECONDS", "30")        # cold Neo4j exceeds the 2.5 s production budget
    from core.config import get_config
    get_config.cache_clear()
    await isolated_graph.reset()
    s, llm = neo4j_isolation, RecordingLLM()
    graphiti = Graphiti(s.uri, s.user, s.password, llm_client=llm, embedder=HashEmbedder(), cross_encoder=ZeroRanker())
    await graphiti.build_indices_and_constraints()
    e = Env(graphiti, llm, monkeypatch)
    monkeypatch.setattr(server, "_graphiti", graphiti)
    persist_outcomes.clear()
    try:
        yield e
    finally:
        await graphiti.close()
        await isolated_graph.reset()


async def mcp(name, **args):
    return await server._tool_call(name, args)


async def seed_source(env):
    """S (knowledge) and the independent control C (project)."""
    await env.add(S_TEXT, K)
    await env.add(C_TEXT, P)
    return await env.uuid_of(SRC)


async def read_source(env, src_uuid):
    """What an MCP client does first: search, and receive the items together with the server's read receipt."""
    found = await mcp("memory.search_knowledge", query="vault passphrase Mira Venn Amber Cabinet", group_id=K, limit=10)
    assert src_uuid in {i["uuid"] for i in found["items"]}
    return found


async def observe(env, *, ask=True):
    """The three observation points, as the set of marker tokens each one delivered."""
    memory = MemoryOps(env.graphiti, "owner")
    searched = await memory.search_memory(QUERY, scopes=["personal", P, K, "experience"], limit=50)
    parts = {"episodes": searched.episodes, "entities": searched.entities, "edges": searched.edges,
             "communities": searched.communities}
    out = {name: set(TOKENS.findall(json.dumps(items, default=str))) for name, items in parts.items()}
    knowledge = await mcp("memory.search_knowledge", query=QUERY, limit=50)
    out["mcp_search"] = set(TOKENS.findall(json.dumps(knowledge["items"], default=str)))
    out["search_all"] = set().union(*(out[n] for n in parts))
    if ask:
        client = RecordingChat()
        env.llm.expect("")
        agent = SimpleChatAgent(client, MemoryOps(env.graphiti, "owner"))
        await agent.answer_core(QUERY)
        await drain(timeout=60)
        out["model_input"] = set(TOKENS.findall(client.text()))
        out["model_calls"] = len(client.calls)
    return out


async def graph_rows(env, token):
    """Every object that still holds the token, whatever its visibility: used to show what was purged vs held."""
    nodes = await env.q("MATCH (n) WHERE any(k IN keys(n) WHERE n[k] IS :: STRING AND n[k] CONTAINS $t) "
                        "RETURN head(labels(n)) AS label, n.uuid AS uuid", t=token)
    edges = await env.q("MATCH ()-[r:RELATES_TO]->() WHERE r.fact CONTAINS $t RETURN 'edge' AS label, r.uuid AS uuid", t=token)
    return nodes + edges


async def erase(env, uuid, hard):
    return await erase_node(env.graphiti, uuid=uuid, hard=hard)
