"""Prototype evidence for the quarantine proposal (verification only).

Real Graphiti.add_episode builds two groups that contain an entity with the SAME
name; one episode of group ``knowledge`` is then erased. The prototype affected
area must (1) find exactly the derived objects through Graphiti lineage, (2) stay
inside the group, (3) show what #54's global quarantine blocks instead, and (4) the
test shows that Graphiti's own remove_episode() purges only the *pure* derived
objects, so *mixed* ones need a rebuild before they can be shown again.
"""
import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from graphiti_core import Graphiti

from tests.support.blast_radius import blast_radius
from tests.support.recording_model import HashEmbedder, RecordingLLM, ZeroRanker

K, P = "knowledge", "project"
EPISODES = [
    (K, "A", "[[Mira Venn]] keeps the vault passphrase ZORBLAX-ALPHA in the [[Amber Cabinet]]."),
    (K, "B", "[[Mira Venn]] meets the courier QUIBBLE-BETA at the [[North Dock]]."),
    (K, "C", "[[Mira Venn]] asks about opening hours of the [[Harbor Office]] today."),
    (P, "P1", "[[Mira Venn]] drafts the plan JELLYFISH-ONE for [[Project Atlas]]."),
    (P, "P2", "[[Mira Venn]] reviews the [[Budget Sheet]] with TOASTER-TWO."),
]


@pytest_asyncio.fixture
async def built(isolated_graph, neo4j_isolation):
    await isolated_graph.reset()
    s, llm = neo4j_isolation, RecordingLLM()
    g = Graphiti(s.uri, s.user, s.password, llm_client=llm, embedder=HashEmbedder(), cross_encoder=ZeroRanker())
    await g.build_indices_and_constraints()
    uuids = {}
    for group, label, body in EPISODES:
        llm.expect(body)
        result = await g.add_episode(name=label, episode_body=body, source_description="proto",
                                     reference_time=datetime.now(timezone.utc), group_id=group)
        uuids[label] = result.episode.uuid
    try:
        yield g, uuids
    finally:
        await g.close()
        await isolated_graph.reset()


async def q(graphiti, cypher, **params):
    return [dict(r) for r in (await graphiti.driver.execute_query(cypher, **params)).records]


@pytest.mark.asyncio
async def test_affected_area_is_found_through_lineage_and_stays_inside_the_group(built):
    g, ids = built
    radius = await blast_radius(g.driver, ids["A"])
    print("OBSERVED_Q " + json.dumps({k: v for k, v in radius.items() if not k.startswith("_")}, sort_keys=True))
    assert radius["group_id"] == K
    assert radius["pure_entities"] == ["Amber Cabinet"]            # only A mentions it
    assert radius["mixed_entities"] == ["Mira Venn"]               # blended with B and C
    assert len(radius["pure_edges"]) == 1 and radius["mixed_edges"] == []
    assert radius["exposed_episodes"] == sorted([ids["B"], ids["C"]])   # later episodes whose window held A
    assert radius["unknown_lineage"] == []

    # Cross-group isolation: the same-named entity of the other group is a different node and is not touched.
    mira = await q(g, "MATCH (n:Entity {name:'Mira Venn'}) RETURN n.uuid AS uuid, n.group_id AS group_id")
    assert sorted(r["group_id"] for r in mira) == [K, P] and len({r["uuid"] for r in mira}) == 2
    foreign = {r["uuid"] for r in mira if r["group_id"] == P}
    foreign |= {r["uuid"] for r in await q(g, "MATCH (n) WHERE n.group_id = $g AND n.uuid IS NOT NULL RETURN n.uuid AS uuid", g=P)}
    area = set(radius["_entity_uuids"]) | set(radius["_edge_uuids"]) | set(radius["exposed_episodes"])
    assert area.isdisjoint(foreign)
    cross = await q(g, "MATCH (a)-[r]->(b) WHERE a.group_id IS NOT NULL AND b.group_id IS NOT NULL AND a.group_id <> b.group_id "
                       "RETURN count(r) AS n")
    assert cross[0]["n"] == 0                                       # no edge ever links two groups


@pytest.mark.asyncio
async def test_global_quarantine_would_block_far_more_than_the_affected_area(built):
    g, ids = built
    radius = await blast_radius(g.driver, ids["A"])
    area_objects = len(radius["_entity_uuids"]) + len(radius["_edge_uuids"])
    total = (await q(g, "MATCH (n:Entity) RETURN count(n) AS n"))[0]["n"] + \
            (await q(g, "MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS n"))[0]["n"]
    other_group = (await q(g, "MATCH (n:Entity {group_id:$g}) RETURN count(n) AS n", g=P))[0]["n"] + \
                  (await q(g, "MATCH ()-[r:RELATES_TO {group_id:$g}]->() RETURN count(r) AS n", g=P))[0]["n"]
    print("OBSERVED_Q " + json.dumps({"derived_objects_total": total, "in_affected_area": area_objects,
                                      "derived_objects_of_other_group": other_group}))
    assert area_objects < total and other_group > 0


@pytest.mark.asyncio
async def test_remove_episode_purges_only_pure_derived_objects(built):
    g, ids = built
    before = await blast_radius(g.driver, ids["A"])
    await g.remove_episode(ids["A"])
    names = {r["name"] for r in await q(g, "MATCH (n:Entity {group_id:$g}) RETURN n.name AS name", g=K)}
    assert "Amber Cabinet" not in names                              # pure: removed with the episode
    assert not [r for r in await q(g, "MATCH ()-[r:RELATES_TO]->() WHERE r.uuid IN $u RETURN r.uuid AS u",
                                   u=before["pure_edges"])]
    mira = (await q(g, "MATCH (n:Entity {name:'Mira Venn', group_id:$g}) RETURN n.summary AS s", g=K))[0]["s"]
    still_blended = "ZORBLAX-ALPHA" in mira
    print("OBSERVED_Q " + json.dumps({"mixed_entity_summary_still_contains_erased_text": still_blended}))
    assert still_blended is True                                     # mixed: must be rebuilt, not just hidden
