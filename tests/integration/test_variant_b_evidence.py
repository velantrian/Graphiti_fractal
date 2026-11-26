"""Variant B: what may reopen an object, and writes that overlap an erasure.

1. A paraphrase of erased text carries none of its tokens: a token screen would call it clean.
   Variant B must hide it anyway and reopen nothing without re-derivation from clean sources.
2. Without an extractor nothing is reopened and the group stays paused.
3. With the production extractor (Graphiti's extract_edges on one live episode, no previous
   episodes) the paraphrase is removed; the recorded extractor input holds no erased text.
4. An erasure that lands while add_episode() is already running: what that write created is
   contained, the group pauses, and the next write after the rebuild is clean.
5. An erasure in another group never disturbs a running write.
"""
import asyncio
import re
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from graphiti_core import Graphiti

from core import erasure_scope
from core.erasure_guard import blocked_node, erase_node, token_hashes, visible_edge
from tests.integration.test_variant_b_vertical import A_TEXT, MARK_A, Env, b_text, delete, tombstone
from tests.support.recording_model import (
    PARAPHRASE, HashEmbedder, ParaphrasingLLM, ZeroRanker, recording_extractor,
)

K, P = "knowledge", "project"
C_TEXT = "[[Mira Venn]] asks about the opening hours of the [[Harbor Office]] today, ref NEWQ1."


@pytest_asyncio.fixture
async def env(isolated_graph, neo4j_isolation, monkeypatch):
    monkeypatch.setattr("knowledge.ingest.get_embedding", AsyncMock(return_value=None))
    monkeypatch.setenv("FRACTAL_API_TOKEN", "vertical-token")   # same helper as the vertical scenario
    monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    s, llm = neo4j_isolation, ParaphrasingLLM()
    graphiti = Graphiti(s.uri, s.user, s.password, llm_client=llm, embedder=HashEmbedder(), cross_encoder=ZeroRanker())
    await graphiti.build_indices_and_constraints()
    e = Env(graphiti, llm, monkeypatch)
    try:
        yield e
    finally:
        await graphiti.close()


async def visible_edges_with(env, text):
    rows = await env.q("MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity) WHERE r.fact CONTAINS $x AND "
                       + visible_edge("r", "s", "t") + " RETURN count(r) AS c", x=text)
    return rows[0]["c"]


async def edges_with(env, text):
    return (await env.q("MATCH ()-[r:RELATES_TO]->() WHERE r.fact CONTAINS $x RETURN count(r) AS c", x=text))[0]["c"]


async def every_holder_is_hidden(env, token):
    nodes = await env.q("MATCH (n) WHERE any(k IN keys(n) WHERE n[k] IS :: STRING AND n[k] CONTAINS $m) "
                        "RETURN (" + blocked_node("n") + ") AS hidden", m=token)
    edges = await env.q("MATCH (s)-[r]->(t) WHERE any(k IN keys(r) WHERE r[k] IS :: STRING AND r[k] CONTAINS $m) "
                        "RETURN NOT (" + visible_edge("r", "s", "t") + ") AS hidden", m=token)
    if not all(h["hidden"] for h in nodes + edges):
        detail = await env.q("MATCH (n) WHERE any(k IN keys(n) WHERE n[k] IS :: STRING AND n[k] CONTAINS $m) AND NOT "
                             + blocked_node("n") + " RETURN labels(n) AS l, n.name AS name, n.erasure_quarantine AS q", m=token)
        detail += await env.q("MATCH (s)-[r]->(t) WHERE any(k IN keys(r) WHERE r[k] IS :: STRING AND r[k] CONTAINS $m) AND "
                              + visible_edge("r", "s", "t") + " RETURN type(r) AS t, r.fact AS fact, r.erasure_quarantine AS q", m=token)
        print("VISIBLE_HOLDERS", token, detail)
    return all(h["hidden"] for h in nodes + edges), len(nodes) + len(edges)


async def contaminated_world(env):
    """A, B1..B3, then C written while A is still live: the model paraphrases A into C's edges."""
    await env.add(A_TEXT)
    for i in (1, 2, 3):
        await env.add(b_text(i))
    ids = {"A": await env.uuid_of(MARK_A)}
    assert (await env.add(C_TEXT))["completeness"] == "complete"
    ids["C"] = await env.uuid_of("NEWQ1")
    assert await edges_with(env, "strongbox") == 1                       # the paraphrase was stored in C's derived edge
    assert await visible_edges_with(env, "strongbox") == 1               # and is delivered to the owner
    return ids


def test_a_token_screen_cannot_see_the_paraphrase():
    erased = set(token_hashes(A_TEXT))
    assert not erased & set(token_hashes(PARAPHRASE))                    # why a screen can never be the basis


@pytest.mark.asyncio
async def test_without_an_extractor_nothing_is_reopened_and_the_group_stays_paused(env, isolated_graph):
    await isolated_graph.reset()
    ids = await contaminated_world(env)
    await delete(env, ids["A"], "hard")
    assert await visible_edges_with(env, "strongbox") == 0               # hidden although screen-clean
    mark = env.llm.mark()
    summary = await erasure_scope.rebuild_area(env.graphiti)             # no extractor
    assert env.llm.input_since(mark) == ""                               # nothing was asked of the model
    assert summary["re_extracted_edges"] == 0 and summary["held_for_missing_extractor"] >= 1
    assert await visible_edges_with(env, "strongbox") == 0 and await visible_edges_with(env, "QUIBBLE") == 0
    assert (await tombstone(env, ids["A"]))["status"] == "SCOPED"
    paused = await env.add("[[Mira Venn]] asks about the [[Budget Sheet]] NEWQ2.", K)
    assert paused["completeness"] == "failed" and "GroupWriteRestricted" in " ".join(paused["warnings"])
    assert env.llm.input_since(mark) == ""                               # still no model call


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["soft", "hard"])
async def test_paraphrase_that_no_live_source_states_stays_restricted_for_reconciliation(env, isolated_graph, mode):
    """The extraction cannot tell an unsupported fact from one it skipped, so it never deletes it."""
    await isolated_graph.reset()
    ids = await contaminated_world(env)
    await delete(env, ids["A"], mode)

    mark = env.llm.mark()
    summary = await erasure_scope.rebuild_area(env.graphiti, extractor=recording_extractor(env.graphiti, env.llm))
    extraction_calls = env.llm.calls[mark:]
    assert extraction_calls                                              # the model was asked, on clean input only
    for call in extraction_calls:
        assert MARK_A not in call.text and "strongbox" not in call.text and "ochre" not in call.text
        assert len(set(re.findall(r"QUIBBLE\d|NEWQ\d", call.text))) <= 1   # one live episode per call, no window
    print("OBSERVED_E " + str({"mode": mode, **summary}))
    assert summary["re_extracted_edges"] >= 3                            # the clean edges are reopened with evidence
    assert summary["held_unmatched_edges"] + summary["held_reworded_edges"] >= 1 and summary["purged_unsupported_edges"] == 0
    assert await edges_with(env, "strongbox") == 1                       # kept for reconciliation, not deleted
    assert await visible_edges_with(env, "strongbox") == 0               # but never served
    evidence = await env.q("MATCH ()-[r:RELATES_TO]->() WHERE r.erasure_evidence IS NOT NULL "
                           "UNWIND r.erasure_evidence AS e RETURN DISTINCT e AS e")
    assert evidence and all(":re_extracted:" in e["e"] for e in evidence)
    assert not any(":content_screen:" in e["e"] for e in evidence)
    assert (await tombstone(env, ids["A"]))["status"] == "SCOPED"        # the group stays paused until reconciled
    mark = env.llm.mark()
    paused = await env.add("[[Mira Venn]] asks about the [[Budget Sheet]] NEWQ2 today.", K)
    assert paused["completeness"] == "failed" and "GroupWriteRestricted" in " ".join(paused["warnings"])
    assert env.llm.input_since(mark) == ""                               # nothing reached the model
    delivered = await env.delivered(K)
    assert MARK_A not in delivered["all"] and "strongbox" not in str(delivered)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["soft", "hard"])
async def test_erasure_during_an_add_episode_that_is_already_running(env, isolated_graph, mode):
    await isolated_graph.reset()
    await env.add(A_TEXT)
    await env.add(b_text(1))
    await env.add(b_text(2))
    a_uuid = await env.uuid_of(MARK_A)

    gate = env.llm.hold_next("extract_nodes.extract_message")           # C's first model call: A is in its input
    env.llm.expect(C_TEXT)
    task = asyncio.create_task(env.add(C_TEXT))
    await asyncio.wait_for(gate.reached.wait(), 30)
    area = await delete(env, a_uuid, mode)                               # erasure lands while add_episode() runs
    assert area["counts"].get("exposed_episode") is not None and await edges_with(env, "strongbox") == 0
    gate.release.set()
    result = await task

    # What the in-flight write created did not exist when the area was computed: it is contained now.
    assert result["completeness"] == "unconfirmed"                       # its commit was refused, not reported as stored
    assert await edges_with(env, "strongbox") == 1                       # the model did restate erased content
    assert await visible_edges_with(env, "strongbox") == 0               # but nobody can be served it
    assert (await every_holder_is_hidden(env, "strongbox"))[0] and (await every_holder_is_hidden(env, MARK_A))[0]
    c_row = (await env.q("MATCH (e:Episodic) WHERE e.content CONTAINS 'NEWQ1' RETURN e.erasure_quarantined AS q"))[0]
    assert c_row["q"] is True
    tomb = await tombstone(env, a_uuid)
    assert tomb["status"] == "SCOPED" and tomb["blocked_groups"] == [K]
    mark = env.llm.mark()
    assert (await env.add("[[Mira Venn]] asks about the [[Budget Sheet]] NEWQ2.", K))["completeness"] == "failed"
    assert env.llm.input_since(mark) == ""

    held = await erasure_scope.rebuild_area(env.graphiti)               # without re-derivation: still paused
    assert (await tombstone(env, a_uuid))["status"] == "SCOPED"
    assert held["re_extracted_edges"] == 0

    await erasure_scope.rebuild_area(env.graphiti, extractor=recording_extractor(env.graphiti, env.llm))
    assert (await tombstone(env, a_uuid))["status"] == "RESOLVED"
    assert await edges_with(env, "strongbox") == 0                      # unsupported (its episode is isolated): deleted

    # the next write after the rebuild
    mark = env.llm.mark()
    done = await env.add("[[Mira Venn]] asks about the [[Budget Sheet]] NEWQ3 today.", K)
    model_input = env.llm.input_since(mark)
    print("OBSERVED_E " + str({"mode": mode, "in_flight_result": result["completeness"],
                               "next_write": done["completeness"], "erased_in_input": MARK_A in model_input,
                               "paraphrase_in_input": "strongbox" in model_input}))
    assert done["completeness"] == "complete"
    assert MARK_A not in model_input and "strongbox" not in model_input and "ochre" not in model_input
    assert "QUIBBLE2" in model_input
    assert (await every_holder_is_hidden(env, MARK_A))[0]


@pytest.mark.asyncio
async def test_erasure_in_another_group_does_not_disturb_a_running_write(env, isolated_graph):
    await isolated_graph.reset()
    await env.add(b_text(1))
    await env.add("[[Mira Venn]] drafts the plan JELLYFISH for [[Project Atlas]].", P)
    p_uuid = await env.uuid_of("JELLYFISH")
    gate = env.llm.hold_next("extract_nodes.extract_message")
    env.llm.expect(C_TEXT)
    task = asyncio.create_task(env.add(C_TEXT))
    await asyncio.wait_for(gate.reached.wait(), 30)
    await delete(env, p_uuid, "hard")                                    # erasure in group project
    gate.release.set()
    result = await task
    assert result["completeness"] == "complete"                         # per-group fence: knowledge is unaffected
    assert (await env.q("MATCH (e:Episodic) WHERE e.content CONTAINS 'NEWQ1' RETURN e.erasure_quarantined AS q"))[0]["q"] is None
