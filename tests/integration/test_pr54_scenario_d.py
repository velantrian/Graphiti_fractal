"""Scenario D (PR #54 verification): consistency of the read paths.

D1  MemoryOps vs knowledge search for an episode marked ``erasure_quarantined``,
    with the global quarantine off and on.
D2  Does the returned text match the current version of the object with that UUID?
D3  Does L3 keep the source revision from its L2 read to saving the profile?
Real: Graphiti search, the admission Cypher, knowledge search, L2/L3 code, ingest and
erasure state on Neo4j. Simulated: the model (recording stub) and, in D2/D3, the timing.
"""
import asyncio
import json
import re
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from graphiti_core import Graphiti

from core.erasure_guard import erase_node, read_erasure_state
from core.memory_ops import MemoryOps
from knowledge.retrieval import search_knowledge
from tests.support import scenario_a_dataset as data
from tests.support.recording_model import HashEmbedder, RecordingLLM, ZeroRanker

MARK = re.compile(r"MK[A-Za-z0-9]+")


def markers(obj):
    return sorted(set(MARK.findall(json.dumps(obj, default=str, ensure_ascii=False))))


@pytest_asyncio.fixture
async def world(isolated_graph, neo4j_isolation, monkeypatch):
    monkeypatch.setattr("knowledge.ingest.get_embedding", AsyncMock(return_value=None))
    await isolated_graph.reset()
    s = neo4j_isolation
    graphiti = Graphiti(s.uri, s.user, s.password, llm_client=RecordingLLM(),
                        embedder=HashEmbedder(), cross_encoder=ZeroRanker())
    await graphiti.build_indices_and_constraints()
    await data.load(graphiti.driver.execute_query, "none")
    await graphiti.driver.execute_query("CALL db.awaitIndexes(120)")

    class W:
        pass

    w = W()
    w.graphiti, w.monkeypatch = graphiti, monkeypatch
    try:
        yield w
    finally:
        await graphiti.close()
        await isolated_graph.reset()


async def _set(w, cypher, **p):
    await w.graphiti.driver.execute_query(cypher, **p)


async def _quarantine_on(w):
    # one erasure elsewhere (a tombstone for an unrelated uuid) switches the global quarantine on
    await _set(w, "CREATE (:FractalErasure {target_uuid:'unrelated', group_id:'project', mode:'hard', "
                  "event_id:'e', created_at:'2026-02-01T00:00:00+00:00'}) "
                  "MERGE (c:FractalErasureControl {key:'global'}) SET c.revision=1")


# --- D1 ---------------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("global_quarantine", [False, True])
@pytest.mark.parametrize("flagged", [False, True])
async def test_d1_erasure_quarantined_flag_across_read_paths(world, global_quarantine, flagged):
    if flagged:
        await _set(world, "MATCH (e:Episodic {uuid:'doc-K'}) SET e.erasure_quarantined=true")
    if global_quarantine:
        await _quarantine_on(world)
    query = "MKdocK"
    memory = await MemoryOps(world.graphiti, "owner").search_memory(query, scopes=["knowledge"], limit=20)
    knowledge = await search_knowledge(world.graphiti, query, group_id="knowledge", limit=20)
    obs = {"global_quarantine": global_quarantine, "episode_flagged_erasure_quarantined": flagged,
           "memory_ops_returns_it": "MKdocK" in markers(memory.episodes),
           "knowledge_search_returns_it": "MKdocK" in markers(knowledge)}
    print("OBSERVED_D1 " + json.dumps(obs, sort_keys=True))
    assert obs["memory_ops_returns_it"] is (not flagged)       # MemoryOps honours the flag
    if not flagged:
        assert obs["knowledge_search_returns_it"] is True       # control: both return it unflagged


@pytest.mark.xfail(strict=True, reason="PR #54 gap: knowledge search ignores erasure_quarantined")
@pytest.mark.asyncio
@pytest.mark.parametrize("global_quarantine", [False, True])
async def test_d1_requirement_knowledge_search_honours_erasure_quarantined(world, global_quarantine):
    await _set(world, "MATCH (e:Episodic {uuid:'doc-K'}) SET e.erasure_quarantined=true")
    if global_quarantine:
        await _quarantine_on(world)
    knowledge = await search_knowledge(world.graphiti, "MKdocK", group_id="knowledge", limit=20)
    assert "MKdocK" not in markers(knowledge)


# --- D2 ---------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_d2_returned_text_is_checked_for_identity_not_for_current_version(world):
    """Search returns text X for an entity; the entity is rewritten before admission."""
    real_search = world.graphiti.search_

    async def search_then_rewrite(*a, **k):
        result = await real_search(*a, **k)
        await _set(world, "MATCH (n:Entity {uuid:'ent-K'}) SET n.summary='REDACTEDV2 current text'")
        return result

    world.graphiti.search_ = search_then_rewrite
    memory = await MemoryOps(world.graphiti, "owner").search_memory("AtlasK MKentK", scopes=["knowledge"], limit=20)
    current = (await world.graphiti.driver.execute_query(
        "MATCH (n:Entity {uuid:'ent-K'}) RETURN n.summary AS s")).records[0]["s"]
    returned = json.dumps(memory.entities, ensure_ascii=False)
    obs = {"current_text_in_database": current, "returned_old_text": "MKentK" in returned,
           "returned_current_text": "REDACTEDV2" in returned}
    print("OBSERVED_D2 " + json.dumps(obs, sort_keys=True))
    assert obs["returned_old_text"] is True and obs["returned_current_text"] is False


# --- D3 ---------------------------------------------------------------------------------
class GatedModel:
    def __init__(self):
        self.reached, self.release, self.prompts = asyncio.Event(), asyncio.Event(), []

    async def __call__(self, messages, context="chat", client=None):
        text = messages[-1]["content"]
        self.prompts.append(text)
        self.reached.set()
        await self.release.wait()
        payload = json.loads(text.split("Input data (JSON):\n", 1)[1].split("\n\n", 1)[0])
        return f"Profile of AtlasK. Based on: {payload['memory_data']}"[:1400]


async def _l3_rows(w):
    rows = (await w.graphiti.driver.execute_query(
        "MATCH (e:Episodic) WHERE e.source_description = 'l3_profile:AtlasK' AND e.uuid <> 'l3-K' "
        "RETURN e.uuid AS uuid, e.content AS content, e.derived_source_ids AS sources, "
        "e.chat_finalized AS fin, e.origin_class AS origin")).records
    return [dict(r) for r in rows]


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_between", [False, True])
async def test_d3_l3_revision_between_l2_read_and_profile_save(world, delete_between):
    from layers.l3_fractal import build_l3_profile, get_l3_fractal_context
    model = GatedModel()
    world.monkeypatch.setattr("layers.l3_fractal.llm_chat_response", model)
    world.monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    from core.config import get_config
    get_config.cache_clear()
    # The dataset's own l3-K profile stores created_at as a string, which would sort ahead of
    # the real Graphiti datetime in get_l3_fractal_context; remove it to keep the control clean.
    await _set(world, "MATCH (e:Episodic {uuid:'l3-K'}) DETACH DELETE e")
    state_at_l2_read = await read_erasure_state(world.graphiti)
    task = asyncio.create_task(build_l3_profile(world.graphiti, "AtlasK", user_id="owner"))
    await model.reached.wait()                       # the L2 context is built and sent to the model
    if delete_between:
        assert (await erase_node(world.graphiti, uuid="doc-K", hard=True))["deleted"] is True
    state_before_save = await read_erasure_state(world.graphiti)
    model.release.set()
    try:
        outcome = {"build_result": "profile returned" if await task else "none"}
    except Exception as exc:  # noqa: BLE001 - the observation is the point
        outcome = {"build_result": f"raised {type(exc).__name__}"}
    rows = await _l3_rows(world)
    readable = await get_l3_fractal_context(world.graphiti, "AtlasK")
    obs = {"delete_between_l2_read_and_save": delete_between,
           "erasure_state_at_l2_read": list(state_at_l2_read), "erasure_state_before_save": list(state_before_save),
           **outcome, "profiles_persisted": len(rows),
           "persisted_profile_contains_doc_K_text": any("MKdocK" in (r["content"] or "") for r in rows),
           "persisted_profile_lists_doc_K_uuid": any("doc-K" in (r["sources"] or []) for r in rows),
           "persisted_profile_finalized_as_complete": bool(rows),
           "readable_through_get_l3_fractal_context": "MKdocK" in readable}
    print("OBSERVED_D3 " + json.dumps(obs, sort_keys=True))
    if not delete_between:
        assert obs["profiles_persisted"] == 1 and obs["readable_through_get_l3_fractal_context"] is True


@pytest.mark.xfail(strict=True, reason="PR #54 gap: L3 does not carry the L2 read revision to the profile save")
@pytest.mark.asyncio
async def test_d3_requirement_profile_from_a_deleted_source_is_not_saved(world):
    from layers.l3_fractal import build_l3_profile
    model = GatedModel()
    world.monkeypatch.setattr("layers.l3_fractal.llm_chat_response", model)
    world.monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    from core.config import get_config
    get_config.cache_clear()
    task = asyncio.create_task(build_l3_profile(world.graphiti, "AtlasK", user_id="owner"))
    await model.reached.wait()
    await erase_node(world.graphiti, uuid="doc-K", hard=True)
    model.release.set()
    try:
        await task
    except Exception:  # noqa: BLE001 - refusing is the required behaviour
        pass
    assert await _l3_rows(world) == []
