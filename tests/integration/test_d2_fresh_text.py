"""D2: a candidate whose text changed between Graphiti's search and the batched admission is excluded.

Real: graphiti_core search (BM25), MemoryOps.search_memory, the admission statement on Neo4j. The change
is made right after Graphiti's search returns, i.e. exactly in the window D2 describes. Each case searches
once with two candidates of the same kind: the rewritten one must disappear, the untouched one must stay.
"""
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from graphiti_core import Graphiti

from core.memory_ops import MemoryOps
from tests.support.recording_model import HashEmbedder, RecordingLLM, ZeroRanker

G = "knowledge"
TOKEN = "D2NEEDLE"
LONG_TAIL = "x" * 9000          # beyond the 8000-character cut applied to displayed episode content


@pytest_asyncio.fixture
async def world(isolated_graph, neo4j_isolation):
    await isolated_graph.reset()
    s = neo4j_isolation
    graphiti = Graphiti(s.uri, s.user, s.password, llm_client=RecordingLLM(), embedder=HashEmbedder(),
                        cross_encoder=ZeroRanker())
    await graphiti.build_indices_and_constraints()
    q = graphiti.driver.execute_query
    await q("""
        UNWIND ['a','b'] AS k
        CREATE (e:Episodic {uuid:'ep-'+k, group_id:$g, name:'ep '+k, content:$t+' episode '+k, source_description:'seed',
                            origin_class:'owner', source:'text', entity_edges:[], created_at:datetime(), valid_at:datetime()})
        CREATE (n:Entity {uuid:'en-'+k, group_id:$g, name:'Needle '+k, summary:$t+' entity '+k, created_at:datetime()})
        CREATE (m:Entity {uuid:'em-'+k, group_id:$g, name:'Mate '+k, summary:'plain', created_at:datetime()})
        CREATE (n)-[:RELATES_TO {uuid:'ed-'+k, group_id:$g, name:'KNOWS', fact:$t+' fact '+k, episodes:['ep-'+k],
                                 created_at:datetime()}]->(m)
        CREATE (c:Community {uuid:'co-'+k, group_id:$g, name:$t+' community '+k, summary:'about '+$t+' '+k,
                             created_at:datetime()})
        CREATE (l:Episodic {uuid:'long-'+k, group_id:$g, name:'long '+k, content:$t+' long '+k+' '+$tail,
                            source_description:'seed', origin_class:'owner', source:'text', entity_edges:[], created_at:datetime(), valid_at:datetime()})
    """, g=G, t=TOKEN, tail=LONG_TAIL)
    await q("CALL db.awaitIndexes(120)")

    class W:
        pass

    w = W()
    w.graphiti, w.q = graphiti, q
    try:
        yield w
    finally:
        await graphiti.close()
        await isolated_graph.reset()


async def search(w, mutate=None, mode="auto"):
    real = w.graphiti.search_

    async def search_then_change(*a, **k):
        result = await real(*a, **k)
        if mutate:
            await w.q(mutate)                 # the change lands after the search, before admission
        return result

    w.graphiti.search_ = search_then_change
    try:
        return await MemoryOps(w.graphiti, "owner").search_memory(
            TOKEN, scopes=[G], limit=50, include_episodes=True, include_entities=True, retrieval_mode=mode)
    finally:
        w.graphiti.search_ = real


def uuids(items):
    return sorted(i["uuid"] for i in items)


CASES = [
    ("episode", "MATCH (e:Episodic {uuid:'ep-a'}) SET e.content = 'rewritten'", "episodes", "ep-a", "ep-b"),
    ("entity summary", "MATCH (n:Entity {uuid:'en-a'}) SET n.summary = 'rewritten'", "entities", "en-a", "en-b"),
    ("entity name", "MATCH (n:Entity {uuid:'en-a'}) SET n.name = 'Renamed'", "entities", "en-a", "en-b"),
    ("edge fact", "MATCH ()-[r:RELATES_TO {uuid:'ed-a'}]->() SET r.fact = 'rewritten'", "edges", "ed-a", "ed-b"),
    ("community", "MATCH (c:Community {uuid:'co-a'}) SET c.summary = 'rewritten'", "communities", "co-a", "co-b"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("label, mutate, bucket, changed, unchanged", CASES, ids=[c[0] for c in CASES])
async def test_changed_candidate_is_excluded_and_unchanged_one_stays(world, label, mutate, bucket, changed, unchanged):
    mode = "drift" if bucket == "communities" else "auto"     # the auto plan does not use communities
    control = await search(world, mode=mode)
    assert {changed, unchanged} <= set(uuids(getattr(control, bucket))), "control: both are found when nothing changes"
    result = await search(world, mutate, mode)
    got = uuids(getattr(result, bucket))
    assert unchanged in got and changed not in got, (label, got)
    assert "_raw_text" not in json.dumps(getattr(result, bucket), default=str)   # internal field never leaks


@pytest.mark.asyncio
async def test_change_beyond_the_display_cut_is_detected_and_unchanged_long_text_stays(world):
    """Episode text is shortened to 8000 characters for output; the comparison uses the text before the cut."""
    result = await search(world, f"MATCH (e:Episodic {{uuid:'long-a'}}) SET e.content = $c".replace("$c", repr(
        f"{TOKEN} long a {LONG_TAIL}" + "CHANGED-AFTER-THE-CUT")))
    got = uuids(result.episodes)
    assert "long-b" in got and "long-a" not in got
    control = await search(world)
    shown = {e["uuid"]: e["content"] for e in control.episodes}
    assert len(shown["long-b"]) == 8000                         # displayed text really is cut


@pytest.mark.asyncio
async def test_candidate_without_raw_text_keeps_identity_only_behaviour(world):
    from core.erasure_guard import admit_search_candidates
    keys = await admit_search_candidates(world.graphiti, [
        {"type": "episode", "uuid": "ep-a", "group_id": G},
        {"type": "entity", "uuid": "en-b", "group_id": G, "_raw_text": ["Needle b", f"{TOKEN} entity b"]},
        {"type": "entity", "uuid": "en-a", "group_id": G, "_raw_text": ["Needle a", "stale summary"]},
    ])
    assert keys == {("episode", "ep-a", G), ("entity", "en-b", G)}
