"""MCP read->write lineage: a retelling carries a server-verified link to the sources it was read from, and the
deletion of a source reaches the retelling AND what Graphiti extracted from it in another group.

Real: graphiti_core 0.29.3 add_episode, MCP tool handlers, ingest claims/finalization, erase_node, area closure, rebuild
with Fractal's production extractor, both search paths and SimpleChatAgent.answer_core. Simulated: both models.
"""
import asyncio

import pytest
from graphiti_core.llm_client.errors import RateLimitError  # noqa: F401

from core import erasure_scope
from core.read_receipts import ReadReceiptRejected
from tests.integration.mcp_lineage_support import (  # noqa: F401  (env is a fixture)
    CONTROL, D_TEXT, K, P, RET, S_TEXT, SRC, env, erase, graph_rows, mcp, observe, read_source, seed_source,
)
from tests.support.recording_model import recording_extractor


async def retell(env, found, src_uuids, text=D_TEXT, tool="memory.remember", group="project", **extra):
    env.llm.expect(text)
    args = {"text": text, "memory_type": group, "source_description": "client_retelling",
            "derived_from": {"receipt_id": found["read_receipt"]["receipt_id"], "source_ids": src_uuids}, **extra}
    return await mcp(tool, **args)


def gone(after, token):
    return all(token not in after[k] for k in ("episodes", "entities", "edges", "communities", "mcp_search", "model_input"))


# --- the contract end to end ----------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("hard", [False, True], ids=["soft", "hard"])
@pytest.mark.parametrize("tool", ["memory.remember", "memory.upload"])
async def test_retelling_with_a_receipt_disappears_with_its_source_everywhere(env, hard, tool):
    src = await seed_source(env)
    found = await read_source(env, src)
    assert found["read_receipt"]["receipt_id"] and src in {s["uuid"] for s in found["read_receipt"]["sources"]}
    stored = await retell(env, found, [src], tool=tool)
    assert stored["completeness"] == "complete"
    lineage = (await env.q("MATCH (e:Episodic {group_id:$g}) WHERE e.content CONTAINS $t "
                           "RETURN e.recall_source_ids AS ids, e.recall_derived AS d, e.origin_class AS o", g=P, t=RET))[0]
    assert lineage == {"ids": [src], "d": True, "o": "agent_derived"}

    before = await observe(env)
    print("BEFORE", {k: sorted(v) for k, v in before.items() if isinstance(v, set)})
    for token in (SRC, RET, CONTROL):                       # recall really ran, on every path
        assert token in before["search_all"] and token in before["mcp_search"] and token in before["model_input"]
    assert RET in before["entities"] and RET in before["edges"], "the summary and the fact existed before deletion"

    await erase(env, src, hard)
    after = await observe(env)
    print("AFTER", {k: sorted(v) for k, v in after.items() if isinstance(v, set)})
    assert gone(after, SRC) and gone(after, RET), "source and dependent texts are absent from both searches and the model input"
    assert CONTROL in after["search_all"] and CONTROL in after["mcp_search"] and CONTROL in after["model_input"]
    held = [r for r in await graph_rows(env, RET) if r["label"] in ("Entity", "edge")]
    assert held == [], "objects extracted only from the dependent episode were removed with it"


@pytest.mark.asyncio
async def test_a_second_independent_source_restores_the_shared_fact_after_a_clean_rebuild(env):
    src = await seed_source(env)
    independent = f"{D_TEXT} The cabinet was inspected on Monday."        # owner-authored, states the same fact
    await env.add(independent, P)
    found = await read_source(env, src)
    await retell(env, found, [src])
    before = await observe(env, ask=False)
    assert RET in before["edges"]

    await erase(env, src, hard=False)
    held = await observe(env, ask=False)
    assert RET not in held["edges"] and RET not in held["entities"], "the shared objects are held, not served, until rebuilt"
    rows = [r for r in await graph_rows(env, RET) if r["label"] in ("Entity", "edge")]
    assert rows, "a shared object is NOT destroyed together with the dependent source"

    await erasure_scope.rebuild_area(env.graphiti, extractor=recording_extractor(env.graphiti, env.llm))
    restored = await observe(env)
    assert RET in restored["edges"] and RET in restored["entities"], "restored from the independent source"
    assert restored["episodes"] >= {RET}, "that independent source itself was never hidden"
    dependent = (await env.q("MATCH (e:Episodic {group_id:$g, origin_class:'agent_derived'}) RETURN "
                             "e.erasure_quarantine AS q, e.uuid AS u", g=P))
    assert dependent and all(v["q"] for v in dependent), "the dependent episode itself stays hidden"
    assert SRC not in restored["search_all"] and SRC not in restored["model_input"]
    assert CONTROL in restored["model_input"]


@pytest.mark.asyncio
async def test_a_second_dependent_retelling_with_the_same_fact_is_not_independent(env):
    """Identical text and a new UUID do not prove independence: a retelling that carries lineage is dependent too."""
    src = await seed_source(env)
    found = await read_source(env, src)
    await retell(env, found, [src])
    twin = f"{D_TEXT} Noted by a second agent."
    found2 = await read_source(env, src)
    await retell(env, found2, [src], text=twin)
    await erase(env, src, hard=False)
    after = await observe(env, ask=False)
    assert RET not in after["search_all"]
    assert [r for r in await graph_rows(env, RET) if r["label"] in ("Entity", "edge")] == [], \
        "no independent source: both dependents' extracted objects were purged"


# --- the write boundary ------------------------------------------------------------------------------------------

async def writes(env):
    return (await env.q("MATCH (e:Episodic {group_id:$g}) WHERE e.content CONTAINS $t RETURN count(e) AS c", g=P, t=RET))[0]["c"]


async def refuse(env, expected, **args):
    calls = len(env.llm.calls)
    with pytest.raises(ReadReceiptRejected) as exc:
        await mcp("memory.remember", text=D_TEXT, memory_type="project", **args)
    assert exc.value.code == expected, exc.value.code
    assert len(env.llm.calls) == calls and await writes(env) == 0, "refused before Graphiti: no model call, no episode"


@pytest.mark.asyncio
async def test_forged_expired_foreign_and_inapplicable_receipts_are_refused_before_graphiti(env, monkeypatch):
    src = await seed_source(env)
    found = await read_source(env, src)
    rid = found["read_receipt"]["receipt_id"]
    other = await env.q("MATCH (e:Episodic {group_id:$g}) RETURN e.uuid AS u LIMIT 1", g=P)
    await refuse(env, "unknown_receipt", derived_from={"receipt_id": "f" * 64, "source_ids": [src]})
    await refuse(env, "source_not_in_receipt", derived_from={"receipt_id": rid, "source_ids": [other[0]["u"]]})
    await refuse(env, "source_not_in_receipt", derived_from={"receipt_id": rid, "source_ids": [src, "made-up"]})
    await refuse(env, "bad_request", derived_from={"receipt_id": rid, "source_ids": []})
    await refuse(env, "bad_request", derived_from={"receipt_id": rid, "source_ids": [src, src]})
    await refuse(env, "bad_request", derived_from="not-an-object")
    await refuse(env, "receipt_required", derivation="retelling")                         # declared, nothing to prove it
    monkeypatch.setenv("FRACTAL_USER_ID", "someone-else")                              # another owner presents it
    await refuse(env, "receipt_not_applicable", derived_from={"receipt_id": rid, "source_ids": [src]})
    monkeypatch.setenv("FRACTAL_USER_ID", "owner")
    await env.q("MATCH (r:FractalReadReceipt {receipt_id:$r}) SET r.expires_at = datetime() - duration('PT1S')", r=rid)
    await refuse(env, "receipt_expired", derived_from={"receipt_id": rid, "source_ids": [src]})


@pytest.mark.asyncio
async def test_a_source_that_changed_or_vanished_after_the_read_invalidates_the_receipt(env):
    src = await seed_source(env)
    found = await read_source(env, src)
    derived = {"receipt_id": found["read_receipt"]["receipt_id"], "source_ids": [src]}
    await env.q("MATCH (e:Episodic {uuid:$u}) SET e.content = e.content + ' (edited)'", u=src)
    await refuse(env, "source_changed", derived_from=derived)
    await env.q("MATCH (e:Episodic {uuid:$u}) SET e.content = replace(e.content, ' (edited)', '')", u=src)
    await refuse_after_delete(env, src, derived)


async def refuse_after_delete(env, src, derived):
    await erase(env, src, hard=False)
    await refuse(env, "source_not_visible", derived_from=derived)
    await env.q("MATCH (e:Episodic {uuid:$u}) DETACH DELETE e", u=src)
    await refuse(env, "source_missing", derived_from=derived)


@pytest.mark.asyncio
async def test_owner_policy_can_require_a_receipt_for_every_agent_write(env, monkeypatch):
    monkeypatch.setenv("FRACTAL_MCP_REQUIRE_READ_RECEIPT", "1")
    await refuse(env, "receipt_required")
    calls = len(env.llm.calls)
    with pytest.raises(ReadReceiptRejected):
        await mcp("memory.upload", text=D_TEXT, memory_type="project")
    assert len(env.llm.calls) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("hard", [False, True], ids=["soft", "hard"])
async def test_source_deleted_while_the_write_runs_leaves_nothing_visible(env, hard):
    src = await seed_source(env)
    found = await read_source(env, src)
    gate = env.llm.hold_next("extract_nodes.extract_message")
    env.llm.expect(D_TEXT)
    task = asyncio.create_task(retell(env, found, [src]))
    await asyncio.wait_for(gate.reached.wait(), 30)
    await erase(env, src, hard)                                      # the source goes while Graphiti is working
    gate.release.set()
    result = await task
    assert result["completeness"] != "complete" and result["unconfirmed_writes"] == 1
    after = await observe(env, ask=True)
    assert gone(after, SRC) and gone(after, RET), "neither the episode nor the graph derived from it is visible"
    assert CONTROL in after["model_input"]
    ep = (await env.q("MATCH (e:Episodic {group_id:$g}) WHERE e.content CONTAINS $t RETURN e.erasure_quarantined AS q, "
                      "e.recall_source_ids AS ids", g=P, t=RET))
    assert ep and ep[0]["q"] is True and ep[0]["ids"] is None, "isolated, never finalized"


# --- repeat and deduplication keep the links -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_repeat_and_deduplication_keep_and_extend_the_links(env):
    src = await seed_source(env)
    found = await read_source(env, src)
    first = await retell(env, found, [src])
    again = await retell(env, found, [src])                      # same text, same receipt: deduplicated
    assert first["completeness"] == "complete" and again["skipped_existing"] == 1
    ids = lambda: env.q("MATCH (e:Episodic {group_id:$g}) WHERE e.content CONTAINS $t RETURN e.recall_source_ids AS ids", g=P, t=RET)
    assert [r["ids"] for r in await ids()] == [[src]]

    other = await env.q("MATCH (e:Episodic {group_id:$g}) WHERE e.content CONTAINS 'JELLYFISH' RETURN e.uuid AS u", g=P)
    found2 = await mcp("memory.search_knowledge", query="walks dog noon Harbor Office", group_id=P)
    again2 = await retell(env, found2, [other[0]["u"]])          # the same text derived from a second source: union
    assert again2["skipped_existing"] == 1
    assert sorted((await ids())[0]["ids"]) == sorted([src, other[0]["u"]])
    await erase(env, src, hard=False)
    assert RET not in (await observe(env, ask=False))["search_all"], "still dependent on the first source"


@pytest.mark.asyncio
async def test_a_retelling_deduplicated_onto_an_owner_episode_does_not_taint_it(env):
    src = await seed_source(env)
    await env.add(D_TEXT, P)                                       # the owner states the same text independently
    found = await read_source(env, src)
    again = await retell(env, found, [src])
    print("DEDUP_ONTO_OWNER", {k: again.get(k) for k in ("completeness", "added", "skipped_existing", "unconfirmed_existing")})
    assert again["added"] == 0                                    # nothing new was written next to the owner's episode
    row = (await env.q("MATCH (e:Episodic {group_id:$g}) WHERE e.content = $t RETURN e.origin_class AS o, "
                       "e.recall_source_ids AS ids", g=P, t=D_TEXT))
    assert row == [{"o": "owner", "ids": None}]
    await erase(env, src, hard=False)
    assert RET in (await observe(env, ask=False))["episodes"], "an independent owner statement is not destroyed with the source"
