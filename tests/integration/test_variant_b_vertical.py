"""Variant B, vertical scenario: delete -> new write -> rebuild -> search again.

Real: graphiti_core 0.29.3 Graphiti.add_episode through Fractal's ingest (claims, fences),
HTTP /delete (soft) or erase_node (hard), the scoped-erasure area/mark/purge, rebuild with
evidence, the admission Cypher and MemoryOps recall. Simulated: only the model (a recorder
that keeps every message of every call).

Data (all through real ingest unless noted):
  group knowledge: A (erased) then 12 later episodes B1..B12 that share the entity "Mira Venn"
    with A: more than the 10-episode previous window, so B11/B12 are reached only through the
    shared entity; group project: P1 with a same-named "Mira Venn"; chat/L3 (Cypher): a chat
    turn in group personal that recalled A, a chain of 12 summaries each derived from the
    previous (deeper than the window), an L3 profile derived from A, plus independent chat
    turns and an independent L3 profile.
"""
import json
import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from graphiti_core import Graphiti
from httpx import ASGITransport, AsyncClient

from core import erasure_scope
from core.erasure_guard import ErasureFenceChanged, blocked_node, erase_node, read_erasure_state
from core.memory_ops import MemoryOps
from knowledge.ingest import ingest_text_document
from tests.support.recording_model import HashEmbedder, RecordingLLM, ZeroRanker

K, P, PERSONAL = "knowledge", "project", "personal"
MARK_A = "ZORBLAX-ALPHA"
CHAIN = 12
TS = "2026-01-01T00:00:00Z"
TOKEN = re.compile(r"ZORBLAX-ALPHA|QUIBBLE\d+|JELLYFISH\w*|CHATKEEP\w*|NEWQ\w*")
A_TEXT = f"[[Mira Venn]] keeps the vault passphrase {MARK_A} in the [[Amber Cabinet]]."


def b_text(i):
    return f"[[Mira Venn]] meets courier QUIBBLE{i} at the [[Place {i}]]."


class Env:
    def __init__(self, graphiti, llm, monkeypatch):
        self.graphiti, self.llm, self.monkeypatch = graphiti, llm, monkeypatch

    async def add(self, body, group=K):
        self.llm.expect(body)
        return await ingest_text_document(self.graphiti, body, group_id=group, user_id="owner")

    async def q(self, cypher, **p):
        return [dict(r) for r in (await self.graphiti.driver.execute_query(cypher, **p)).records]

    async def uuid_of(self, marker):
        rows = await self.q("MATCH (e:Episodic) WHERE e.content CONTAINS $m RETURN e.uuid AS u", m=marker)
        assert len(rows) == 1, (marker, rows)
        return rows[0]["u"]

    async def delivered(self, group):
        query = " ".join([MARK_A, "Mira Venn Amber Cabinet CHATKEEP NEWQ JELLYFISH"] + [f"QUIBBLE{i}" for i in range(1, CHAIN + 1)])
        memory = MemoryOps(self.graphiti, "owner")
        result = await memory.search_memory(query, scopes=[group], limit=50)
        def tokens(items):
            return set(TOKEN.findall(json.dumps(items, default=str)))
        out = {"episodes": tokens(result.episodes), "derived": tokens([*result.entities, *result.edges, *result.communities])}
        out["all"] = out["episodes"] | out["derived"]
        return out

    async def visible_count(self, label, condition):
        alias = "e" if label == "Episodic" else "n"
        rows = await self.q(f"MATCH ({alias}:{label}) WHERE {condition} AND NOT " + blocked_node(alias)
                            + " RETURN count(*) AS c")
        return rows[0]["c"]

    async def visible_edges(self, text):
        from core.erasure_guard import visible_edge
        rows = await self.q("MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity) WHERE r.fact CONTAINS $x AND "
                            + visible_edge("r", "s", "t") + " RETURN count(r) AS c", x=text)
        return rows[0]["c"]

    async def delivered_chat(self):
        rows = await self.q("MATCH (e:Episodic {group_id:$g}) WHERE " + "NOT " + blocked_node("e")
                            + " RETURN e.uuid AS u", g=PERSONAL)
        return {r["u"] for r in rows}


@pytest_asyncio.fixture
async def env(isolated_graph, neo4j_isolation, monkeypatch):
    monkeypatch.setattr("knowledge.ingest.get_embedding", AsyncMock(return_value=None))
    monkeypatch.setenv("FRACTAL_API_TOKEN", "vertical-token")
    monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    await isolated_graph.reset()
    s, llm = neo4j_isolation, RecordingLLM()
    graphiti = Graphiti(s.uri, s.user, s.password, llm_client=llm, embedder=HashEmbedder(), cross_encoder=ZeroRanker())
    await graphiti.build_indices_and_constraints()
    e = Env(graphiti, llm, monkeypatch)
    try:
        yield e
    finally:
        await graphiti.close()
        await isolated_graph.reset()


async def build(env):
    ids = {}
    assert (await env.add(A_TEXT))["completeness"] == "complete"
    ids["A"] = await env.uuid_of(MARK_A)
    for i in range(1, CHAIN + 1):
        assert (await env.add(b_text(i)))["completeness"] == "complete"
        ids[f"B{i}"] = await env.uuid_of(f"QUIBBLE{i}")
    assert (await env.add("[[Mira Venn]] drafts the plan JELLYFISH for [[Project Atlas]].", P))["completeness"] == "complete"
    ids["P1"] = await env.uuid_of("JELLYFISH")
    mira = (await env.q("MATCH (n:Entity {name:'Mira Venn', group_id:$g}) RETURN n.uuid AS u", g=K))[0]["u"]

    def chat(uuid, **extra):
        return {"uuid": uuid, "group_id": PERSONAL, "name": uuid, "content": f"chat {uuid}", "source": "text",
                "source_description": "chat", "episode_kind": "chat_turn", "chat_finalized": True,
                "origin_class": "agent_derived", "entity_edges": [], "valid_at": TS, "created_at": TS, **extra}

    nodes = [chat("chat-A", recall_source_ids=[ids["A"], mira], content=f"recalled {MARK_A}"),
             chat("chat-P1", recall_source_ids=[ids["P1"]], content="CHATKEEPP depends on project only"),
             chat("chat-free", recall_source_ids=[], content="CHATKEEPFREE no memory used")]
    prev = "chat-A"
    for i in range(1, CHAIN + 1):
        nodes.append(chat(f"sum-{i}", episode_kind="chat_summary", derived_source_ids=[prev],
                          summarized_turns=[prev], content=f"summary {i}"))
        prev = f"sum-{i}"
    for n in nodes:
        await env.q("CREATE (:Episodic $p)", p=n)
    for uuid, sources, text in (("l3-A", [ids["A"], ids["B1"]], f"L3 profile built from {MARK_A}"),
                                ("l3-B3", [ids["B3"]], "L3 profile CHATKEEPL3 from B3 only")):
        await env.q("CREATE (:Episodic $p)", p={
            "uuid": uuid, "group_id": K, "name": uuid, "content": text, "source": "text", "entity_edges": [],
            "source_description": "l3_profile:Mira Venn", "origin_class": "agent_derived",
            "derived_source_ids": sources, "valid_at": TS, "created_at": TS})
    await env.q("CALL db.awaitIndexes(120)")
    return ids


async def http_soft_delete(env, uuid):
    from app import app

    async def dep():
        return env.graphiti

    env.monkeypatch.setattr("app.get_graphiti_dep", dep)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/delete", json={"uuid": uuid, "hard": False},
                                     headers={"Authorization": "Bearer vertical-token"})
    assert response.status_code == 200 and response.json()["deleted"] is True
    return response.json()


async def delete(env, uuid, mode):
    if mode == "soft":
        return (await http_soft_delete(env, uuid))["area"]
    return (await erase_node(env.graphiti, uuid=uuid, hard=True))["area"]


async def tombstone(env, uuid):
    return (await env.q("MATCH (t:FractalErasure {target_uuid:$u}) RETURN properties(t) AS t", u=uuid))[0]["t"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["soft", "hard"])
async def test_vertical_delete_new_write_rebuild_search(env, mode):
    ids = await build(env)
    before = (await env.delivered(K))["all"]
    assert MARK_A in before and "QUIBBLE12" in before                      # control: found before the erasure

    # 1. delete: tombstone + area, computed through lineage, immediately
    area = await delete(env, ids["A"], mode)
    counts = area["counts"]
    tomb = await tombstone(env, ids["A"])
    print("OBSERVED_V " + json.dumps({"mode": mode, "area": counts, "blocked_groups": tomb["blocked_groups"],
                                       "derived_groups": area["derived_groups"], "status": tomb["status"]}, sort_keys=True))
    assert tomb["status"] == "SCOPED" and tomb["blocked_groups"] == [K]
    assert counts["entity_pure"] == 1 and counts["purged_entities"] == 1 and counts["purged_edges"] == 1   # Amber Cabinet + A's edge
    assert counts["entity_mixed"] == 1                                                   # Mira Venn (K) blended with B1..B12
    assert counts["exposed_episode"] == CHAIN                                            # 12 > the 10-episode window
    assert counts["derived_episode"] == 1 + CHAIN + 1                                    # chat-A, sum-1..12, l3-A (cross-group chain)
    assert PERSONAL in area["derived_groups"]

    # 2. new write right now: paused only in the affected group; nothing reaches the model
    mark = env.llm.mark()
    paused = await env.add("[[Mira Venn]] asks about the [[Harbor Office]] NEWQ1.", K)
    assert paused["completeness"] == "failed" and "GroupWriteRestricted" in " ".join(paused["warnings"])
    assert env.llm.input_since(mark) == ""
    mark = env.llm.mark()
    other = await env.add("[[Mira Venn]] reviews the [[Budget Sheet]] NEWQ2 today.", P)       # independent group
    assert other["completeness"] == "complete"
    other_input = env.llm.input_since(mark)
    assert "JELLYFISH" in other_input and MARK_A not in other_input and "QUIBBLE" not in other_input

    # 3. reads while the area is open: the area is hidden, independent objects stay available
    seen = await env.delivered(K)
    assert MARK_A not in seen["all"]
    assert await env.visible_count("Episodic", "e.content CONTAINS 'QUIBBLE'") == CHAIN   # source texts stay available
    assert await env.visible_count("Entity", "n.summary CONTAINS 'QUIBBLE'") == 0         # derived objects wait for evidence
    assert await env.visible_edges("QUIBBLE") == 0
    assert not {f"QUIBBLE{i}" for i in range(1, CHAIN + 1)} & seen["derived"]
    assert "JELLYFISH" in (await env.delivered(P))["all"]
    chat_visible = await env.delivered_chat()
    assert chat_visible == {"chat-P1", "chat-free"}                        # chat-A and sum-1..12 hidden, others kept
    l3 = await env.q("MATCH (e:Episodic) WHERE e.uuid IN ['l3-A','l3-B3'] AND NOT " + blocked_node("e") + " RETURN e.uuid AS u")
    assert {r["u"] for r in l3} == {"l3-B3"}

    # 4. rebuild: evidence per object, tombstone kept, group resolved
    summary = await erasure_scope.rebuild_area(env.graphiti)
    tomb = await tombstone(env, ids["A"])
    print("OBSERVED_V " + json.dumps({"mode": mode, "rebuild": summary, "status": tomb["status"]}, sort_keys=True))
    assert tomb["status"] == "RESOLVED" and tomb["blocked_groups"] == [] and tomb["owner_decision_needed"] == 1 + CHAIN + 1
    mira = (await env.q("MATCH (n:Entity {name:'Mira Venn', group_id:$g}) RETURN n.summary AS s, n.erasure_evidence AS ev, "
                        "size(coalesce(n.erasure_quarantine,[])) AS marks", g=K))[0]
    assert MARK_A not in mira["s"] and "QUIBBLE12" in mira["s"] and mira["marks"] == 0
    assert any(":rebuilt_from_clean_edges:" in e for e in mira["ev"])
    edge = (await env.q("MATCH ()-[r:RELATES_TO]->() WHERE r.fact CONTAINS 'QUIBBLE11' RETURN r.erasure_evidence AS ev"))[0]
    assert any(":content_screen:" in e for e in edge["ev"])                # reopened by screen, evidence recorded

    # 5. new write after the rebuild: the recorder sees the valid context and not the erased text
    mark = env.llm.mark()
    done = await env.add("[[Mira Venn]] asks about the [[Harbor Office]] NEWQ3 today.", K)
    model_input = env.llm.input_since(mark)
    print("OBSERVED_V " + json.dumps({"mode": mode, "write_after_rebuild": done["completeness"],
                                       "deleted_marker_in_model_input": MARK_A in model_input,
                                       "valid_markers_in_model_input": sorted(set(re.findall(r"QUIBBLE\d+", model_input)))[:3]}))
    assert done["completeness"] == "complete"
    assert MARK_A not in model_input and "QUIBBLE12" in model_input and "Amber Cabinet" not in model_input

    # 6. saved derived content and search again
    summary_after = (await env.q("MATCH (n:Entity {name:'Mira Venn', group_id:$g}) RETURN n.summary AS s", g=K))[0]["s"]
    assert MARK_A not in summary_after and "Harbor Office" in summary_after + json.dumps(
        await env.q("MATCH ()-[r:RELATES_TO]->() RETURN r.fact AS f"))
    after = await env.delivered(K)
    assert MARK_A not in after["all"] and "NEWQ3" in after["all"]
    assert await env.visible_edges("QUIBBLE") == CHAIN                           # all 12 cleared by evidence, shown again
    assert await env.visible_count("Entity", "n.summary CONTAINS 'QUIBBLE12'") >= 1 # incl. the rebuilt Mira Venn
    assert after["derived"] & {f"QUIBBLE{i}" for i in range(1, CHAIN + 1)}
    holders = await env.q("MATCH (n) WHERE any(k IN keys(n) WHERE n[k] IS :: STRING AND n[k] CONTAINS $m) "
                          "RETURN n.uuid AS u, (" + blocked_node("n") + ") AS hidden", m=MARK_A)
    assert holders and all(h["hidden"] for h in holders)                  # whoever still stores it is hidden
    rel_holders = await env.q("MATCH ()-[r]->() WHERE any(k IN keys(r) WHERE r[k] IS :: STRING AND r[k] CONTAINS $m) "
                              "RETURN count(r) AS c", m=MARK_A)
    assert rel_holders[0]["c"] == 0                                       # no edge keeps the erased text
    assert await env.delivered_chat() == {"chat-P1", "chat-free"}         # chain stays hidden: owner decision


@pytest.mark.asyncio
async def test_re_erasure_during_rebuild_commits_nothing_then_converges(env):
    ids = await build(env)
    await delete(env, ids["A"], "hard")
    fired = {}

    async def second_deletion(name):
        if name == "after_screen" and not fired:
            fired["done"] = True
            await erase_node(env.graphiti, uuid=ids["B1"], hard=True)       # a new erasure mid-rebuild

    env.monkeypatch.setattr(erasure_scope, "_checkpoint", second_deletion)
    with pytest.raises(ErasureFenceChanged):
        await erasure_scope.rebuild_area(env.graphiti)
    env.monkeypatch.undo()
    env.monkeypatch.setattr("knowledge.ingest.get_embedding", AsyncMock(return_value=None))
    assert fired
    mira = (await env.q("MATCH (n:Entity {name:'Mira Venn', group_id:$g}) RETURN n.summary AS s, "
                        "size(coalesce(n.erasure_quarantine,[])) AS marks", g=K))[0]
    assert mira["marks"] >= 1 and MARK_A in mira["s"]                     # nothing was reopened by the aborted run
    assert (await env.add("[[Mira Venn]] asks [[Harbor Office]] NEWQ4.", K))["completeness"] == "failed"

    await erasure_scope.rebuild_area(env.graphiti)                          # converges for both erasures
    states = {t["u"]: t["s"] for t in await env.q("MATCH (t:FractalErasure) RETURN t.target_uuid AS u, t.status AS s")}
    assert set(states.values()) == {"RESOLVED"} and set(states) == {ids["A"], ids["B1"]}
    summary = (await env.q("MATCH (n:Entity {name:'Mira Venn', group_id:$g}) RETURN n.summary AS s", g=K))[0]["s"]
    assert MARK_A not in summary and "QUIBBLE1 " not in summary + " " and "QUIBBLE2" in summary
    mark = env.llm.mark()
    done = await env.add("[[Mira Venn]] asks about the [[Harbor Office]] NEWQ5 today.", K)
    model_input = env.llm.input_since(mark)
    assert done["completeness"] == "complete"
    assert MARK_A not in model_input and not re.search(r"QUIBBLE1(?!\d)", model_input) and "QUIBBLE12" in model_input
