"""Atomic closing of an erasure event (case 2 of the final review of #58).

Real: erase_node, rebuild_area, mark_late_write, require_group_writable, the visibility predicates,
on a disposable Neo4j with two independent connections. No model is involved: the late write is
what add_episode leaves behind (an episode, an entity it mentions, an edge it sourced), written
with plain Cypher exactly where the in-flight call would have written it.

The decision RESOLVED vs SCOPED and the registration of a late write run under one serialization
(the erasure control lock). The tests cover: a late write arriving before the close statement
(the window between "count the marks" and "close" in the old two-step code), after it, and
concurrently with it; the tombstone status, admission of the next write, and an independent group.
"""
import asyncio

import pytest
import pytest_asyncio

from core import erasure_scope
from core.erasure_guard import GroupWriteRestricted, erase_node, require_group_writable, visible_node
from core.erasure_scope import mark_late_write, rebuild_area
from tests.integration.conftest import IsolatedGraph
from tests.integration.test_erasure_idempotency import seed
from tests.support.neo4j_isolation import open_admitted_async_driver

G, H = "close-g", "close-h"


@pytest_asyncio.fixture
async def two(isolated_graph, neo4j_isolation):
    driver = await open_admitted_async_driver(neo4j_isolation)
    try:
        yield isolated_graph, IsolatedGraph(driver, neo4j_isolation)
    finally:
        await driver.close()


async def q(graph, cypher, **p):
    return [dict(r) for r in (await graph.driver.execute_query(cypher, **p)).records]


async def late_write(graph, name, group=G):
    """What an add_episode that started before the erasure leaves behind."""
    await q(graph, """
        CREATE (e:Episodic {uuid:$n, group_id:$g, content:'late', name:$n, created_at:datetime(), valid_at:datetime()})
        CREATE (a:Entity {uuid:$n+'-a', group_id:$g, name:'LateA', summary:'late'})
        CREATE (b:Entity {uuid:$n+'-b', group_id:$g, name:'LateB', summary:'late'})
        CREATE (e)-[:MENTIONS]->(a), (e)-[:MENTIONS]->(b)
        CREATE (a)-[:RELATES_TO {uuid:$n+'-r', group_id:$g, name:'KNOWS', fact:'late fact', episodes:[$n]}]->(b)
    """, n=name, g=group)


async def tomb(graph, uuid="ep-1"):
    return (await q(graph, "MATCH (t:FractalErasure {target_uuid:$u}) RETURN properties(t) AS t", u=uuid))[0]["t"]


async def marks(graph, name):
    ents = await q(graph, "MATCH (a:Entity {uuid:$n+'-a'}) RETURN a.erasure_quarantine AS e", n=name)
    rels = await q(graph, "MATCH ()-[r:RELATES_TO {uuid:$n+'-r'}]->() RETURN r.erasure_quarantine AS r", n=name)
    return (ents[0]["e"] or [] if ents else []), (rels[0]["r"] or [] if rels else [])


async def writable(graph, group):
    try:
        await require_group_writable(graph, group)
        return True
    except GroupWriteRestricted:
        return False


async def visible(graph, uuid):
    return (await q(graph, f"MATCH (n:Entity {{uuid:$u}}) WHERE {visible_node('n')} RETURN count(n) AS c", u=uuid))[0]["c"]


async def setup(g1):
    await seed(g1, "ep-1", group=G)              # erased episode in group G
    await seed(g1, "indep", text="Cy walks the dog at noon.", group=H)   # independent group
    first = await erase_node(g1, uuid="ep-1")
    return first["event_id"], first["event_revision"] - 1     # event id, revision a write "started" at


def assert_reopened(status_row, ev):
    assert status_row["status"] == "SCOPED" and status_row.get("resolved_at") is None, status_row
    assert status_row["blocked_groups"] == [G]


@pytest.mark.asyncio
async def test_late_write_before_the_close_keeps_the_event_open_and_the_group_paused(two, monkeypatch):
    g1, g2 = two
    ev, since = await setup(g1)
    await late_write(g1, "late-1")
    fired = []

    async def hook(name):
        if name == "before_close" and not fired:
            fired.append(name)                  # the late write registers right before the closing statement
            await mark_late_write(g2, G, since, ["late-1"])

    monkeypatch.setattr(erasure_scope, "_checkpoint", hook)
    await rebuild_area(g1, ev)
    assert fired
    t = await tomb(g1)
    assert_reopened(t, ev)
    entity_marks, edge_marks = await marks(g1, "late-1")
    assert any(m.startswith(ev + ":") for m in entity_marks) and any(m.startswith(ev + ":") for m in edge_marks)
    assert await writable(g1, G) is False, "the next write in the group must be refused"
    assert await writable(g1, H) is True, "an independent group is not paused"
    assert await visible(g1, "late-1-a") == 0
    assert await visible(g1, "indep-a") == 1 and await visible(g1, "indep-b") == 1
    assert await q(g1, "MATCH (t:FractalErasure {group_id:$h}) RETURN t", h=H) == []


@pytest.mark.asyncio
async def test_late_write_after_the_close_reopens_the_resolved_event(two):
    g1, g2 = two
    ev, since = await setup(g1)
    await rebuild_area(g1, ev)
    assert (await tomb(g1))["status"] == "RESOLVED" and await writable(g1, G) is True
    await late_write(g1, "late-2")
    result = await mark_late_write(g2, G, since, ["late-2"])
    assert result["events"] == [ev] and result["marked"] == 3
    assert_reopened(await tomb(g1), ev)
    assert await writable(g1, G) is False and await writable(g1, H) is True
    assert await visible(g1, "late-2-a") == 0


@pytest.mark.asyncio
async def test_a_write_that_started_after_the_erasure_is_not_a_late_write(two):
    g1, g2 = two
    ev, since = await setup(g1)
    await rebuild_area(g1, ev)
    await late_write(g1, "after-1")
    current = (await tomb(g1))["revision"]
    assert (await mark_late_write(g2, G, current, ["after-1"]))["marked"] == 0
    assert (await tomb(g1))["status"] == "RESOLVED" and await writable(g1, G) is True


@pytest.mark.asyncio
async def test_split_close_loses_the_late_write__control_experiment(two, monkeypatch):
    """The OLD two-step closing (count marks, then write the status) with the late write landing
    between the two steps: the event ends RESOLVED while the late objects are marked and the group
    is writable again. This is the defect the single-statement close removes."""
    g1, g2 = two
    ev, since = await setup(g1)
    await late_write(g1, "late-3")
    prefix = ev + ":"
    left = await q(g1, """OPTIONAL MATCH (n:Entity) WHERE any(m IN coalesce(n.erasure_quarantine,[]) WHERE m STARTS WITH $p)
                          RETURN count(n) AS c""", p=prefix)
    assert left[0]["c"] == 0                                         # step 1: nothing marked yet
    await mark_late_write(g2, G, since, ["late-3"])                  # the late write lands right here
    await q(g1, "MATCH (t:FractalErasure {event_id:$e}) SET t.status='RESOLVED', t.blocked_groups=[]", e=ev)   # step 2
    t = await tomb(g1)
    entity_marks, _ = await marks(g1, "late-3")
    assert t["status"] == "RESOLVED" and entity_marks and await writable(g1, G) is True   # the defect, reproduced


@pytest.mark.asyncio
async def test_close_and_late_write_race_never_leaves_marks_under_a_resolved_event(two):
    """Concurrent on two connections, several rounds, either start order. Whatever the interleaving:
    marked late objects <=> open event and paused group. (If the registration lands before the
    rebuild looks at the area, the rebuild may purge the late objects - their only source is the
    quarantined late episode - and the event then legitimately resolves with nothing marked.)"""
    g1, g2 = two
    outcomes = set()
    for i in range(12):
        await g1.reset()
        ev, since = await setup(g1)
        name = f"race-{i}"
        await late_write(g1, name)
        closer, late = rebuild_area(g1, ev), mark_late_write(g2, G, since, [name])
        await asyncio.gather(*((closer, late) if i % 2 else (late, closer)))
        t = await tomb(g1)
        entity_marks, edge_marks = await marks(g1, name)
        marked = any(m.startswith(ev + ":") for m in entity_marks + edge_marks)
        if marked:
            assert t["status"] == "SCOPED" and t["blocked_groups"] == [G], (i, t)
            assert await writable(g1, G) is False
            outcomes.add("marked-and-open")
        else:
            assert t["status"] == "RESOLVED" and await writable(g1, G) is True, (i, t)
            outcomes.add("purged-and-resolved")
        assert await writable(g1, H) is True
    assert outcomes, outcomes


@pytest.mark.asyncio
async def test_late_write_of_the_chat_path_is_contained_in_any_group(two):
    """The chat path is fenced globally: an erasure in ANY group newer than the write's snapshot
    reopens that event, and the chat group's objects are marked and paused."""
    g1, g2 = two
    ev, since = await setup(g1)
    await rebuild_area(g1, ev)
    await late_write(g1, "chat-late", group="personal")
    result = await mark_late_write(g2, "personal", since, ["chat-late"], any_group=True)
    assert result["events"] == [ev]
    t = await tomb(g1)
    assert t["status"] == "SCOPED" and "personal" in t["blocked_groups"]
    assert await writable(g1, "personal") is False and await writable(g1, H) is True
