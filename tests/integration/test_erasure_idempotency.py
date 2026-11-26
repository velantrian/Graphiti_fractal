"""Idempotent erasure requests on a disposable Neo4j (case 3 of the final review of #58).

Real: erase_node, scope_erasure, rebuild_area, the HTTP /delete route; every statement goes to
Neo4j. Two *independent* connections = two admitted AsyncDriver objects with their own connection
pools. No model is involved (the area is seeded with plain Cypher).

Contract under test
  * one erasure event per target UUID: event_id, event_revision and the seed never change;
  * event_revision (database revision at which this event was created) is not the current
    database revision (global, grows with every NEW event);
  * the response states the mode that was actually performed: soft | hard | none;
  * hard after soft physically deletes the node without reopening the event or bumping revisions;
  * repeats never reopen a RESOLVED event and never create a second tombstone.
"""
import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from core.erasure_guard import erase_node, read_erasure_state
from core.erasure_scope import rebuild_area
from tests.integration.conftest import IsolatedGraph
from tests.support.neo4j_isolation import open_admitted_async_driver

G = "idem"


@pytest_asyncio.fixture
async def two(isolated_graph, neo4j_isolation):
    """Two independent connections to the same admitted database."""
    driver = await open_admitted_async_driver(neo4j_isolation)
    other = IsolatedGraph(driver, neo4j_isolation)
    try:
        yield isolated_graph, other
    finally:
        await driver.close()


async def q(graph, cypher, **p):
    return [dict(r) for r in (await graph.driver.execute_query(cypher, **p)).records]


async def seed(graph, uuid="ep-1", text="Ada keeps the key in the cabinet.", group=G):
    """Episode that mentions two entities joined by one edge sourced only by this episode."""
    await q(graph, """
        CREATE (e:Episodic {uuid:$u, group_id:$g, content:$c, name:'n', created_at:datetime(), valid_at:datetime(),
                            entity_edges:[$ru]})
        CREATE (a:Entity {uuid:$u+'-a', group_id:$g, name:'Ada', summary:'s'})
        CREATE (b:Entity {uuid:$u+'-b', group_id:$g, name:'Cabinet', summary:'s'})
        CREATE (e)-[:MENTIONS]->(a), (e)-[:MENTIONS]->(b)
        CREATE (a)-[:RELATES_TO {uuid:$ru, group_id:$g, name:'KEEPS', fact:$c, episodes:[$u]}]->(b)
    """, u=uuid, g=group, c=text, ru=uuid + "-r")


async def tombstones(graph, uuid):
    return await q(graph, "MATCH (t:FractalErasure {target_uuid:$u}) RETURN properties(t) AS t", u=uuid)


async def node_state(graph, uuid):
    rows = await q(graph, "MATCH (n:Episodic {uuid:$u}) RETURN n.deleted AS d", u=uuid)
    return "absent" if not rows else ("soft" if rows[0]["d"] else "live")


async def db_revision(graph):
    return (await read_erasure_state(graph))[0]


@pytest.mark.asyncio
async def test_concurrent_soft_deletes_on_two_connections_share_one_event(two):
    g1, g2 = two
    await seed(g1)
    results = await asyncio.gather(*[erase_node(g1 if i % 2 else g2, uuid="ep-1") for i in range(8)])
    fresh = [r for r in results if r["performed_mode"] == "soft"]
    repeats = [r for r in results if r["performed_mode"] == "none"]
    assert len(fresh) == 1 and len(repeats) == 7
    assert {r["event_id"] for r in results} == {fresh[0]["event_id"]}
    assert {r["event_revision"] for r in results} == {1}
    assert all(r["deleted"] is True and r["requested_mode"] == "soft" for r in results)
    assert all(r["idempotent_repeat"] is True for r in repeats) and fresh[0]["idempotent_repeat"] is False
    rows = await tombstones(g1, "ep-1")
    assert len(rows) == 1 and rows[0]["t"]["event_id"] == fresh[0]["event_id"]
    assert await db_revision(g1) == 1, "seven repeats must not bump the database revision"
    assert await node_state(g1, "ep-1") == "soft"
    assert fresh[0]["area"]["status"] == "SCOPED"


@pytest.mark.asyncio
async def test_concurrent_requests_for_different_targets_get_distinct_events(two):
    g1, g2 = two
    await seed(g1, "ep-1")
    await seed(g1, "ep-2", text="Bo hides the map in the attic.")
    a, b = await asyncio.gather(erase_node(g1, uuid="ep-1"), erase_node(g2, uuid="ep-2"))
    assert a["event_id"] != b["event_id"]
    assert sorted([a["event_revision"], b["event_revision"]]) == [1, 2]
    assert await db_revision(g1) == 2


@pytest.mark.asyncio
async def test_event_revision_is_not_the_current_database_revision(two):
    g1, _ = two
    await seed(g1, "ep-1")
    await seed(g1, "ep-2", text="Bo hides the map in the attic.")
    first = await erase_node(g1, uuid="ep-1")
    second = await erase_node(g1, uuid="ep-2")
    again = await erase_node(g1, uuid="ep-1")
    assert (first["event_revision"], first["database_revision"]) == (1, 1)
    assert (second["event_revision"], second["database_revision"]) == (2, 2)
    assert again["event_id"] == first["event_id"]
    assert (again["event_revision"], again["database_revision"]) == (1, 2)
    assert again["performed_mode"] == "none"


@pytest.mark.asyncio
async def test_repeat_after_completed_rebuild_never_reopens_the_event(two):
    g1, g2 = two
    await seed(g1)
    first = await erase_node(g1, uuid="ep-1")
    done = await rebuild_area(g1, first["event_id"])
    assert done.get("status") != "unknown_or_closed_event"
    before = (await tombstones(g1, "ep-1"))[0]["t"]
    assert before["status"] == "RESOLVED", before
    rev = await db_revision(g1)
    again = await erase_node(g2, uuid="ep-1")
    assert again["performed_mode"] == "none" and again["event_id"] == first["event_id"]
    assert again["event_status"] == "RESOLVED"
    after = (await tombstones(g1, "ep-1"))[0]["t"]
    assert after == before, "a repeat must not touch a resolved tombstone"
    assert await db_revision(g1) == rev
    assert (await read_erasure_state(g1))[1] is False


@pytest.mark.asyncio
async def test_repeat_after_physical_deletion_reads_the_tombstone(two):
    g1, g2 = two
    await seed(g1)
    first = await erase_node(g1, uuid="ep-1", hard=True)
    assert first["performed_mode"] == "hard" and first["deleted"] is True
    assert await node_state(g1, "ep-1") == "absent"
    rev = await db_revision(g1)
    for hard in (False, True):
        again = await erase_node(g2, uuid="ep-1", hard=hard)
        assert again["deleted"] is True and again["found"] is True
        assert again["performed_mode"] == "none" and again["requested_mode"] == ("hard" if hard else "soft")
        assert again["event_id"] == first["event_id"] and again["event_revision"] == first["event_revision"]
        assert again["node_state"] == "absent"
    assert await db_revision(g1) == rev
    assert len(await tombstones(g1, "ep-1")) == 1


@pytest.mark.asyncio
async def test_unknown_uuid_is_not_found_and_creates_nothing(two):
    g1, _ = two
    result = await erase_node(g1, uuid="never-existed", hard=True)
    assert result["deleted"] is False and result["found"] is False and result["performed_mode"] == "none"
    assert result["event_id"] is None and result["erasure_revision"] is None
    assert await tombstones(g1, "never-existed") == []
    assert await db_revision(g1) == 0


@pytest.mark.asyncio
async def test_hard_after_soft_deletes_physically_without_a_new_event(two):
    g1, g2 = two
    await seed(g1)
    soft = await erase_node(g1, uuid="ep-1")
    await rebuild_area(g1, soft["event_id"])
    before = (await tombstones(g1, "ep-1"))[0]["t"]
    assert before["status"] == "RESOLVED" and await node_state(g1, "ep-1") == "soft"
    rev = await db_revision(g1)
    hard = await erase_node(g2, uuid="ep-1", hard=True)
    assert hard["performed_mode"] == "hard" and hard["requested_mode"] == "hard"
    assert hard["event_id"] == soft["event_id"] and hard["event_revision"] == soft["event_revision"]
    assert hard["idempotent_repeat"] is True and hard["event_mode"] == "soft"
    assert await node_state(g1, "ep-1") == "absent"
    after = (await tombstones(g1, "ep-1"))[0]["t"]
    assert after["status"] == "RESOLVED", "physical deletion must not reopen a resolved event"
    assert after["event_id"] == before["event_id"] and after["revision"] == before["revision"]
    assert after["seed_entities"] == before["seed_entities"] and after["token_hashes"] == before["token_hashes"]
    assert after.get("hard_deleted_at"), "the upgrade is recorded on the event"
    assert await db_revision(g1) == rev
    third = await erase_node(g1, uuid="ep-1", hard=True)
    assert third["performed_mode"] == "none" and third["node_state"] == "absent"


@pytest.mark.asyncio
async def test_hard_after_soft_while_scoping_is_still_open_keeps_the_event_open(two):
    g1, _ = two
    await seed(g1)
    soft = await erase_node(g1, uuid="ep-1")
    await q(g1, "MATCH (t:FractalErasure {target_uuid:'ep-1'}) SET t.status='PENDING_SCOPE'")
    hard = await erase_node(g1, uuid="ep-1", hard=True)
    assert hard["performed_mode"] == "hard" and hard["event_id"] == soft["event_id"]
    row = (await tombstones(g1, "ep-1"))[0]["t"]
    assert row["status"] == "PENDING_SCOPE" and row["seed_entities"], "seed survives physical deletion"


@pytest.mark.asyncio
async def test_concurrent_soft_and_hard_converge_to_one_event_and_a_physical_delete(two):
    g1, g2 = two
    await seed(g1)
    a, b = await asyncio.gather(erase_node(g1, uuid="ep-1"), erase_node(g2, uuid="ep-1", hard=True))
    assert a["event_id"] == b["event_id"]
    assert await node_state(g1, "ep-1") == "absent"
    assert sorted([a["performed_mode"], b["performed_mode"]]) in (["hard", "none"], ["hard", "soft"])
    assert len(await tombstones(g1, "ep-1")) == 1 and await db_revision(g1) == 1


@pytest.mark.asyncio
async def test_legacy_flag_without_tombstone_becomes_a_normal_event(two):
    g1, _ = two
    await seed(g1)
    await q(g1, "MATCH (e:Episodic {uuid:'ep-1'}) SET e.deleted=true")
    result = await erase_node(g1, uuid="ep-1")
    assert result["performed_mode"] == "soft" and result["idempotent_repeat"] is False
    assert len(await tombstones(g1, "ep-1")) == 1


@pytest.mark.asyncio
async def test_http_delete_reports_the_performed_mode(two, monkeypatch):
    from app import app
    g1, g2 = two
    await seed(g1)
    monkeypatch.setenv("FRACTAL_API_TOKEN", "idem-token")
    monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    monkeypatch.setenv("FRACTAL_ALLOW_HARD_DELETE", "1")

    async def dep():
        return g2

    monkeypatch.setattr("app.get_graphiti_dep", dep)
    headers = {"Authorization": "Bearer idem-token"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def call(uuid, hard):
            return (await client.post("/delete", json={"uuid": uuid, "hard": hard}, headers=headers)).json()
        r1, r2 = await call("ep-1", False), await call("ep-1", False)
        r3, r4 = await call("ep-1", True), await call("ep-1", True)
        r5 = await call("missing", False)
    assert [r["performed_mode"] for r in (r1, r2, r3, r4)] == ["soft", "none", "hard", "none"]
    assert [r["requested_mode"] for r in (r1, r2, r3, r4)] == ["soft", "soft", "hard", "hard"]
    assert {r["event_id"] for r in (r1, r2, r3, r4)} == {r1["event_id"]}
    assert all(r["status"] == "ok" for r in (r1, r2, r3, r4))
    assert r5["status"] == "not_found" and r5["performed_mode"] == "none"


@pytest.mark.asyncio
async def test_concurrent_scoping_of_one_event_runs_once(two):
    from core.erasure_scope import scope_erasure
    g1, g2 = two
    await seed(g1)
    await q(g1, "MATCH (e:Episodic {uuid:'ep-1'}) SET e.deleted=true")
    from core import erasure_guard
    await erasure_guard.ensure_erasure_constraints(g1)
    await q(g1, """MATCH (e:Episodic {uuid:'ep-1'})
        CREATE (t:FractalErasure {target_uuid:'ep-1', event_id:'ev-x', group_id:$g, mode:'soft', status:'PENDING_SCOPE',
            target_label:'Episodic', valid_at:'', seed_entities:['ep-1-a','ep-1-b'], seed_edges:['ep-1-r'],
            token_hashes:[], blocked_groups:[$g], revision:1})""", g=G)
    a, b, c = await asyncio.gather(*[scope_erasure(x, "ev-x") for x in (g1, g2, g1)])
    worked = [r for r in (a, b, c) if r.get("status") == "SCOPED"]
    assert len(worked) == 1, (a, b, c)
    assert all(r.get("scoping") == "in_progress_elsewhere" or r.get("status") == "SCOPED" for r in (a, b, c))
    row = (await tombstones(g1, "ep-1"))[0]["t"]
    assert row["status"] == "SCOPED" and row.get("scope_lease") is None
