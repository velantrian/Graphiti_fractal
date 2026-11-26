"""rebuild_area(): three review cases, on a disposable Neo4j.

R1  a fact stated only by the *second* live source of an edge must reopen it (every live source is
    consulted before the edge's fate is decided; the first source not stating it is not a verdict).
R2  one pair of entities with different and opposite relations: direction and meaning are kept; an
    ambiguous match, a reversed-only match or a fact the extraction skipped leaves the edge restricted.
R3  an unknown or closed event_id changes nothing, however many events are open, and a valid id
    touches only its own event.
"""
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from graphiti_core import Graphiti

from core import erasure_scope
from core.erasure_guard import erase_node, visible_edge
from tests.integration.test_variant_b_vertical import A_TEXT, MARK_A, Env, b_text
from tests.support.recording_model import HashEmbedder, RecordingLLM, ZeroRanker, recording_extractor

K, P = "knowledge", "project"
TS = "2026-01-01T00:00:00Z"


@pytest_asyncio.fixture
async def env(isolated_graph, neo4j_isolation, monkeypatch):
    monkeypatch.setattr("knowledge.ingest.get_embedding", AsyncMock(return_value=None))
    s, llm = neo4j_isolation, RecordingLLM()
    graphiti = Graphiti(s.uri, s.user, s.password, llm_client=llm, embedder=HashEmbedder(), cross_encoder=ZeroRanker())
    await graphiti.build_indices_and_constraints()
    e = Env(graphiti, llm, monkeypatch)
    try:
        yield e
    finally:
        await graphiti.close()


async def edge_rows(env, where="true"):
    return await env.q(f"""MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity) WHERE {where}
        RETURN r.uuid AS uuid, r.name AS name, s.name AS src, t.name AS dst, r.fact AS fact,
               size(coalesce(r.erasure_quarantine,[])) AS marks, r.erasure_evidence AS ev, r.episodes AS episodes""")


async def fingerprint(env):
    nodes = await env.q("MATCH (n) RETURN labels(n) AS l, properties(n) AS p")
    rels = await env.q("MATCH ()-[r]->() RETURN type(r) AS t, properties(r) AS p")
    rows = sorted(json.dumps([r["l"], r["p"]], sort_keys=True, default=str) for r in nodes) + \
        sorted(json.dumps([r["t"], r["p"]], sort_keys=True, default=str) for r in rels)
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()


# --- R1 ---------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_r1_a_fact_confirmed_only_by_the_second_live_source_reopens_the_edge(env, isolated_graph):
    await isolated_graph.reset()
    await env.add("[[Mira Venn]] keeps the vault passphrase ZORBLAX-ALPHA.")
    await env.add("[[Mira Venn]] chats with the [[Dock Master]] about the tides.")                 # S1: no Harbor relation
    await env.add("[[Mira Venn]] asks about the opening hours of the [[Harbor Office]].")          # S2: states it
    a = await env.uuid_of(MARK_A)
    s1, s2 = await env.uuid_of("Dock Master"), await env.uuid_of("Harbor Office")
    # A merged edge as Graphiti keeps it: first source S1, then S2; its text blends the erased text in.
    await env.q("MATCH (:Entity {name:'Mira Venn'})-[r:RELATES_TO]->(:Entity {name:'Harbor Office'}) "
                "SET r.episodes=[$s1,$s2], r.fact='Mira Venn keeps ZORBLAX-ALPHA near the Harbor Office'", s1=s1, s2=s2)
    await erase_node(env.graphiti, uuid=a, hard=True)
    assert (await edge_rows(env, "t.name='Harbor Office'"))[0]["marks"] == 1

    summary = await erasure_scope.rebuild_area(env.graphiti, extractor=recording_extractor(env.graphiti, env.llm))
    edge = (await edge_rows(env, "t.name='Harbor Office'"))
    print("OBSERVED_R1 " + json.dumps({"edges": [{k: e[k] for k in ("src", "dst", "fact", "marks", "episodes")} for e in edge],
                                       "evidence": edge[0]["ev"] if edge else None, "summary": summary}, default=str))
    assert len(edge) == 1, "the edge supported by the second source was deleted"
    assert edge[0]["marks"] == 0 and edge[0]["episodes"] == [s1, s2]
    assert "ZORBLAX" not in edge[0]["fact"] and "opening hours" in edge[0]["fact"]
    assert any(":re_extracted:" in e and ":src2:" in e and f":via{s2[:8]}" in e for e in edge[0]["ev"])    # both live sources were consulted


# --- R2 ---------------------------------------------------------------------------------------
async def relation_world(env, edges):
    """A (erased, mentions Mira), then S mentioning Mira and Harbor; hand-made edges between them."""
    await env.add("[[Mira Venn]] keeps the vault passphrase ZORBLAX-ALPHA.")
    await env.add("[[Mira Venn]] and the [[Harbor Office]] work on the quarterly schedule.")
    a, s = await env.uuid_of(MARK_A), await env.uuid_of("quarterly schedule")
    await env.q("MATCH (:Entity {name:'Mira Venn'})-[r:RELATES_TO]->(:Entity {name:'Harbor Office'}) DELETE r")
    ids = {r["name_or_key"]: r for r in []}
    for key, src, dst, name, fact in edges:
        await env.q("MATCH (a:Entity {name:$src}), (b:Entity {name:$dst}) CREATE (a)-[:RELATES_TO {uuid:$u, group_id:'knowledge', "
                    "name:$name, fact:$fact, episodes:[$s], created_at:$ts, fact_embedding:[0.1,0.1]}]->(b)",
                    src=src, dst=dst, u=key, name=name, fact=fact, s=s, ts=TS)
    await erase_node(env.graphiti, uuid=a, hard=True)
    return s


def scripted(facts):
    async def extract(episode_uuid):
        return [{"source": src, "target": dst, "name": name, "fact": fact, "input_sha256": "scripted"}
                for src, dst, name, fact in facts]
    return extract


async def entity_uuid(env, name):
    return (await env.q("MATCH (n:Entity {name:$n}) RETURN n.uuid AS u", n=name))[0]["u"]


@pytest.mark.asyncio
async def test_r2_different_and_opposite_relations_keep_direction_and_meaning(env, isolated_graph):
    await isolated_graph.reset()
    await relation_world(env, [
        ("e-visits", "Mira Venn", "Harbor Office", "VISITS", "STALE visits ZORBLAX"),
        ("e-owns", "Mira Venn", "Harbor Office", "OWNS", "STALE owns ZORBLAX"),
        ("e-employs", "Harbor Office", "Mira Venn", "EMPLOYS", "STALE employs ZORBLAX")])
    m, h = await entity_uuid(env, "Mira Venn"), await entity_uuid(env, "Harbor Office")
    summary = await erasure_scope.rebuild_area(env.graphiti, extractor=scripted([
        (h, m, "EMPLOYS", "The Harbor Office employs Mira Venn."),          # listed first on purpose
        (m, h, "OWNS", "Mira Venn owns the Harbor Office."),
        (m, h, "VISITS", "Mira Venn visits the Harbor Office weekly.")]))
    rows = {r["uuid"]: r for r in await edge_rows(env)}
    assert (rows["e-visits"]["src"], rows["e-visits"]["dst"], rows["e-visits"]["fact"]) == ("Mira Venn", "Harbor Office", "Mira Venn visits the Harbor Office weekly.")
    assert (rows["e-owns"]["src"], rows["e-owns"]["dst"], rows["e-owns"]["fact"]) == ("Mira Venn", "Harbor Office", "Mira Venn owns the Harbor Office.")
    assert (rows["e-employs"]["src"], rows["e-employs"]["dst"], rows["e-employs"]["fact"]) == ("Harbor Office", "Mira Venn", "The Harbor Office employs Mira Venn.")
    assert all(r["marks"] == 0 for r in rows.values()) and summary["re_extracted_edges"] >= 3
    assert {r["name"] for r in rows.values()} >= {"VISITS", "OWNS", "EMPLOYS"}            # relation type untouched


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["ambiguous_same_relation", "reversed_only", "skipped"])
async def test_r2_unclear_matches_stay_restricted_for_reconciliation(env, isolated_graph, case):
    await isolated_graph.reset()
    await relation_world(env, [
        ("e-1", "Mira Venn", "Harbor Office", "KNOWS", "STALE one ZORBLAX"),
        ("e-2", "Mira Venn", "Harbor Office", "KNOWS", "STALE two ZORBLAX")] if case == "ambiguous_same_relation" else
        [("e-1", "Mira Venn", "Harbor Office", "KNOWS", "STALE one ZORBLAX")])
    m, h = await entity_uuid(env, "Mira Venn"), await entity_uuid(env, "Harbor Office")
    facts = {"ambiguous_same_relation": [(m, h, "KNOWS", "Mira Venn knows the office from the old days."),
                                         (m, h, "KNOWS", "Mira Venn knows the office manager well.")],
             "reversed_only": [(h, m, "KNOWS", "The Harbor Office knows Mira Venn.")],      # opposite direction only
             "skipped": []}[case]
    summary = await erasure_scope.rebuild_area(env.graphiti, extractor=scripted(facts))
    rows = await edge_rows(env, "r.uuid STARTS WITH 'e-'")
    assert rows and all(r["marks"] == 1 for r in rows)                                   # still restricted
    assert all("ZORBLAX" in r["fact"] and r["ev"] is None for r in rows)                  # nothing was rewritten
    assert summary["re_extracted_edges"] == 0 and summary["purged_unsupported_edges"] == 0
    assert (summary["held_ambiguous_edges"] if case == "ambiguous_same_relation" else summary["held_unmatched_edges"]) == len(rows)
    visible = await env.q("MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity) WHERE r.uuid STARTS WITH 'e-' AND "
                          + visible_edge("r", "s", "t") + " RETURN count(r) AS c")
    assert visible[0]["c"] == 0 and (await env.add("[[Mira Venn]] asks [[Budget Sheet]] NEWQ9.", K))["completeness"] == "failed"


# --- R3 ---------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_r3_unknown_or_closed_event_id_changes_nothing_with_two_open_events(env, isolated_graph):
    await isolated_graph.reset()
    await env.add(A_TEXT)
    await env.add(b_text(1))
    await env.add("[[Mira Venn]] drafts the plan JELLYFISH for [[Project Atlas]].", P)
    await env.add("[[Mira Venn]] reviews the [[Budget Sheet]] with TOASTER.", P)
    a, p1 = await env.uuid_of(MARK_A), await env.uuid_of("JELLYFISH")
    ev_a = (await erase_node(env.graphiti, uuid=a, hard=True))["event_id"]
    ev_p = (await erase_node(env.graphiti, uuid=p1, hard=True))["event_id"]
    await env.q("CREATE (:FractalErasure {target_uuid:'pending-x', event_id:'evt-pending', group_id:'knowledge', "
                "status:'PENDING_SCOPE', target_label:'Episodic', seed_entities:[], seed_edges:[], valid_at:'2026-01-01T00:00:00Z', "
                "revision:99, token_hashes:[], blocked_groups:['knowledge']})")
    await env.q("CREATE (:FractalErasure {target_uuid:'closed-x', event_id:'evt-closed', group_id:'knowledge', "
                "status:'RESOLVED', revision:1, token_hashes:[], blocked_groups:[]})")
    states = lambda: env.q("MATCH (t:FractalErasure) RETURN t.event_id AS e, t.status AS s ORDER BY e")  # noqa: E731
    open_before = {r["e"]: r["s"] for r in await states()}
    assert open_before[ev_a] == open_before[ev_p] == "SCOPED" and open_before["evt-pending"] == "PENDING_SCOPE"
    before = await fingerprint(env)
    extractor = recording_extractor(env.graphiti, env.llm)

    for bad in ("no-such-event", "evt-closed"):
        mark = env.llm.mark()
        result = await erasure_scope.rebuild_area(env.graphiti, event_id=bad, extractor=extractor)
        assert result["status"] == "unknown_or_closed_event" and result["event_id"] == bad
        assert result["re_extracted_edges"] == 0 and env.llm.input_since(mark) == ""      # nothing was even asked
        assert await fingerprint(env) == before                                           # not a single property changed
    assert {r["e"]: r["s"] for r in await states()} == open_before

    # a valid id touches only its own event
    marks_p_before = await env.q("MATCH (n) WHERE any(m IN coalesce(n.erasure_quarantine,[]) WHERE m STARTS WITH $p) "
                                 "RETURN n.uuid AS u ORDER BY u", p=ev_p + ":")
    await erasure_scope.rebuild_area(env.graphiti, event_id=ev_a, extractor=extractor)
    after = {r["e"]: r["s"] for r in await states()}
    assert after[ev_a] == "RESOLVED" and after[ev_p] == "SCOPED" and after["evt-pending"] == "PENDING_SCOPE"
    assert marks_p_before == await env.q("MATCH (n) WHERE any(m IN coalesce(n.erasure_quarantine,[]) WHERE m STARTS WITH $p) "
                                         "RETURN n.uuid AS u ORDER BY u", p=ev_p + ":")


# --- R4 ---------------------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("old_text, new_text", [
    ("Mira Venn meets the courier on 5 May.", "Mira Venn meets the courier on 7 May."),          # another date
    ("Mira Venn owns 3 offices at the harbor.", "Mira Venn owns 5 offices at the harbor.")])    # another number
async def test_r4_changed_date_or_number_keeps_the_old_edge_restricted_and_exact_second_source_wins(
        env, isolated_graph, old_text, new_text):
    """One edge, one clean fact with the same ends, direction and relation but another date or number:
    the old UUID must not take a different meaning. In the same run an edge whose exact statement is
    confirmed by the second source is reopened although the first source says a similar, different fact."""
    await isolated_graph.reset()
    await env.add("[[Mira Venn]] keeps the vault passphrase ZORBLAX-ALPHA.")
    await env.add("[[Mira Venn]] and the [[Harbor Office]] met in the morning.")                   # first source
    await env.add("[[Mira Venn]] and the [[Harbor Office]] met again in the evening.")             # second source
    a, s1, s2 = await env.uuid_of(MARK_A), await env.uuid_of("in the morning"), await env.uuid_of("in the evening")
    await env.q("MATCH (:Entity {name:'Mira Venn'})-[r:RELATES_TO]->(:Entity {name:'Harbor Office'}) DELETE r")
    stored_two = "Mira Venn reports to the Harbor Office on 5 May."
    for key, name, fact, episodes in (("e-old", "MEETS", old_text, [s1]),
                                       ("e-two", "REPORTS", stored_two, [s1, s2])):
        await env.q("MATCH (a:Entity {name:'Mira Venn'}), (b:Entity {name:'Harbor Office'}) CREATE (a)-[:RELATES_TO {uuid:$u, "
                    "group_id:'knowledge', name:$n, fact:$f, episodes:$e, created_at:$ts, fact_embedding:[0.1,0.1]}]->(b)",
                    u=key, n=name, f=fact, e=episodes, ts=TS)
    await erase_node(env.graphiti, uuid=a, hard=True)
    m, h = await entity_uuid(env, "Mira Venn"), await entity_uuid(env, "Harbor Office")
    by_episode = {
        s1: [(m, h, "MEETS", new_text),                                           # same relation, other date/number
             (m, h, "REPORTS", "Mira Venn reports to the Harbor Office on 7 May.")],   # similar but different fact
        s2: [(m, h, "REPORTS", stored_two)]}                                      # exact confirmation, second source

    async def extractor(episode_uuid):
        return [{"source": x, "target": y, "name": n, "fact": f, "input_sha256": "scripted"}
                for x, y, n, f in by_episode[episode_uuid]]

    summary = await erasure_scope.rebuild_area(env.graphiti, extractor=extractor)
    rows = {r["uuid"]: r for r in await edge_rows(env, "r.uuid STARTS WITH 'e-'")}
    print("OBSERVED_R4 " + json.dumps({"old": {k: rows["e-old"][k] for k in ("fact", "marks", "ev")},
                                       "two": {k: rows["e-two"][k] for k in ("fact", "marks", "ev")}, "summary": summary}))
    # the old UUID keeps its old meaning and stays restricted for reconciliation
    assert rows["e-old"]["fact"] == old_text and rows["e-old"]["marks"] == 1 and rows["e-old"]["ev"] is None
    assert summary["held_meaning_changed_edges"] == 1
    # the exact confirmation of the second source is taken into account
    assert rows["e-two"]["fact"] == stored_two and rows["e-two"]["marks"] == 0
    assert any(f":via{s2[:8]}" in e and ":src2:" in e for e in rows["e-two"]["ev"])
    assert "7 May" not in rows["e-two"]["fact"]
    tomb = await env.q("MATCH (t:FractalErasure {target_uuid:$u}) RETURN t.status AS s", u=a)
    assert tomb[0]["s"] == "SCOPED"                                               # the group stays paused for the held edge
