"""Scenario B (PR #54 verification): what a NEW write sees after a completed delete.

Real components: graphiti_core 0.29.3 ``Graphiti.add_episode`` (previous-episode
retrieval, entity/edge extraction and resolution, summary building, persistence),
Fractal's ingest path with its erasure fence, the HTTP ``/delete`` endpoint, the
erasure admission Cypher and ``MemoryOps.search_memory``, all on a disposable
Neo4j. Simulated: only the model. ``RecordingLLM`` keeps every message of every
model call and answers with schema-valid output; the embedder is a hash.

Three facts are reported separately per variant:
  1. MODEL INPUT   - the deleted marker / the valid marker appears in a model call
  2. DERIVED STORE - the deleted marker appears in an object written during the new write
  3. OWNER DELIVERY - the deleted marker is returned by a public read afterwards
"""
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from graphiti_core import Graphiti
from httpx import ASGITransport, AsyncClient

from core import erasure_scope
from core.erasure_guard import read_erasure_state
from core.memory_ops import MemoryOps
from knowledge.ingest import ingest_text_document
from tests.support.recording_model import HashEmbedder, RecordingLLM, ZeroRanker

GROUP = "knowledge"
A = "[[Mira Venn]] keeps the vault passphrase ZORBLAX-ALPHA in the [[Amber Cabinet]]."
B = "[[Mira Venn]] meets the courier QUIBBLE-BETA at the [[North Dock]]."
C = "[[Mira Venn]] asks about opening hours of the [[Harbor Office]] today."
MARK_A, MARK_B = "ZORBLAX-ALPHA", "QUIBBLE-BETA"


class Env:
    def __init__(self, graphiti, llm):
        self.graphiti, self.llm = graphiti, llm

    async def add(self, body):
        self.llm.expect(body)
        return await ingest_text_document(self.graphiti, body, group_id=GROUP, user_id="owner")

    async def query(self, cypher, **params):
        return (await self.graphiti.driver.execute_query(cypher, **params)).records


@pytest_asyncio.fixture
async def env(isolated_graph, neo4j_isolation, monkeypatch):
    monkeypatch.setattr("knowledge.ingest.get_embedding", AsyncMock(return_value=None))
    await isolated_graph.reset()
    llm = RecordingLLM()
    s = neo4j_isolation
    graphiti = Graphiti(s.uri, s.user, s.password, llm_client=llm,
                        embedder=HashEmbedder(), cross_encoder=ZeroRanker())
    await graphiti.build_indices_and_constraints()
    try:
        yield Env(graphiti, llm)
    finally:
        await graphiti.close()
        await isolated_graph.reset()


async def _uuid_of(env, marker):
    rows = await env.query("MATCH (e:Episodic) WHERE e.content CONTAINS $m RETURN e.uuid AS uuid", m=marker)
    assert len(rows) == 1
    return rows[0]["uuid"]


async def _snapshot(env):
    """(label, uuid) -> text of every Entity/Episodic/edge, to find what a write changed."""
    out = {}
    for r in await env.query("MATCH (n:Entity) RETURN n.uuid AS u, n.name AS name, n.summary AS t"):
        out[("entity", r["u"])] = f"{r['name']}: {r['t']}"
    for r in await env.query("MATCH ()-[r:RELATES_TO]->() RETURN r.uuid AS u, r.fact AS t"):
        out[("edge", r["u"])] = r["t"]
    for r in await env.query("MATCH (e:Episodic) RETURN e.uuid AS u, e.content AS t"):
        out[("episode", r["u"])] = r["t"]
    return out


async def _deliver(env):
    memory = MemoryOps(env.graphiti, "owner")
    texts = []
    for query in ("Mira Venn passphrase", MARK_A, "Amber Cabinet", "Mira Venn"):
        result = await memory.search_memory(query, scopes=[GROUP], limit=20)
        for item in [*result.episodes, *result.entities, *result.edges, *result.communities]:
            texts.append(json.dumps(item, default=str))
    return "\n".join(texts)


async def _http_soft_delete(env, monkeypatch, uuid):
    from app import app

    async def dep():
        return env.graphiti

    monkeypatch.setenv("FRACTAL_API_TOKEN", "scenario-b-token")
    monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    monkeypatch.setattr("app.get_graphiti_dep", dep)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/delete", json={"uuid": uuid, "hard": False},
                                     headers={"Authorization": "Bearer scenario-b-token"})
    assert response.status_code == 200 and response.json()["deleted"] is True
    return response.json()


async def run_variant(env, monkeypatch, mode, *, filter_previous=False):
    await env.add(A)
    await env.add(B)
    a_uuid = await _uuid_of(env, MARK_A)
    delivered_before = MARK_A in await _deliver(env)
    if mode == "soft":
        await _http_soft_delete(env, monkeypatch, a_uuid)
    elif mode == "hard":
        from core.erasure_guard import erase_node
        assert (await erase_node(env.graphiti, uuid=a_uuid, hard=True))["deleted"] is True
    rebuilt = None
    if mode in ("soft", "hard"):
        # Variant B pauses model-assisted writes in the affected group until its derived
        # objects are rebuilt or cleared (refusal itself is covered in test_variant_b_vertical).
        rebuilt = await erasure_scope.rebuild_area(env.graphiti)
    state_before = await read_erasure_state(env.graphiti)
    before = await _snapshot(env)
    residue_before = sorted(f"{k[0]}" for k, v in before.items() if MARK_A in v and k[0] != "episode")
    mark = env.llm.mark()
    if filter_previous:
        # Hypothetical minimal fix: hand Graphiti an explicit previous-episode list
        # that leaves out the deleted episode (measurement only, not production code).
        b_uuid = await _uuid_of(env, MARK_B)
        env.llm.expect(C)
        await env.graphiti.add_episode(
            name="c", episode_body=C, source_description="scenario-b",
            reference_time=datetime.now(timezone.utc), group_id=GROUP, previous_episode_uuids=[b_uuid])
        result = {"completeness": "n/a (direct add_episode)"}
    else:
        result = await env.add(C)
    state_after = await read_erasure_state(env.graphiti)
    after = await _snapshot(env)

    model_input = env.llm.input_since(mark)
    calls_with_a = sorted({c.prompt_name for c in env.llm.calls[mark:] if MARK_A in c.text})
    changed = {k: v for k, v in after.items() if before.get(k) != v}
    delivered = await _deliver(env)
    return {
        "mode": mode, "rebuild": rebuilt,
        "erasure_state_before_after_new_write": [list(state_before), list(state_after)],
        "new_write_completeness": result["completeness"],
        "model_calls_for_new_write": len(env.llm.calls) - mark,
        "1_model_input": {"deleted_marker_present": MARK_A in model_input,
                          "valid_marker_present": MARK_B in model_input,
                          "prompts_containing_deleted_marker": calls_with_a},
        "2_derived_store": {"derived_objects_with_deleted_marker_before_new_write": residue_before,
                            "entities_written_with_deleted_marker": sorted(
                                v.split(":")[0] for k, v in changed.items() if k[0] == "entity" and MARK_A in v),
                            "objects_written_by_new_write": len(changed),
                            "written_objects_containing_deleted_marker":
                                sorted(f"{k[0]}" for k, v in changed.items() if MARK_A in v),
                            "deleted_episode_node_still_stored": ("episode", a_uuid) in after},
        "3_owner_delivery": {"deleted_marker_returned_before_delete": delivered_before,
                             "deleted_marker_returned_after": MARK_A in delivered,
                             "valid_marker_returned_after": MARK_B in delivered,
                             "new_text_returned_after": "Harbor Office" in delivered},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["none", "soft", "hard"])
async def test_scenario_b_observation(env, monkeypatch, mode):
    observed = await run_variant(env, monkeypatch, mode)
    print("OBSERVED " + json.dumps(observed, sort_keys=True))
    # Harness validity, true in every variant.
    assert observed["new_write_completeness"] == "complete"
    assert observed["model_calls_for_new_write"] >= 3
    assert observed["1_model_input"]["valid_marker_present"] is True   # positive control


# Requirement (task statement): at an unchanged erasure revision a deleted text must not be
# part of the model input of a new write, while valid context still is. Failed on PR #54
# @698239b (strict xfail there); variant B makes it a mandatory passing test.
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["soft", "hard"])
async def test_requirement_deleted_text_absent_from_model_input(env, monkeypatch, mode):
    observed = await run_variant(env, monkeypatch, mode)
    assert observed["1_model_input"]["valid_marker_present"] is True
    assert observed["1_model_input"]["deleted_marker_present"] is False
