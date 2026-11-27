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



@pytest.mark.asyncio
@pytest.mark.parametrize("hard", [False, True], ids=["soft", "hard"])
async def test_retelling_of_cross_group_entity_is_transitively_contained(env, hard):
    """Receipt lineage alternates episode -> extracted entity -> episode across groups."""
    src = await seed_source(env)
    first = await read_source(env, src)
    assert (await retell(env, first, [src]))["completeness"] == "complete"
    second = await mcp("memory.search_knowledge", query="Mira Venn vault secret phrase", group_id=P)
    entity = next(row for row in second["items"] if row["kind"] == "Entity" and RET in row["text"])
    body = f"[[Quill Raven]] repeats the vault secret phrase {RET} for [[Glass Door]]."
    assert (await retell(env, second, [entity["uuid"]], text=body, group="personal"))["completeness"] == "complete"
    before = await observe(env, ask=False)
    assert RET in before["episodes"] and RET in before["edges"]
    await erase(env, src, hard=hard)
    rows = await env.q(
        "MATCH (e:Episodic) WHERE e.content IN [$first, $second] "
        "RETURN e.group_id AS group, coalesce(e.erasure_quarantine,[]) AS marks",
        first=D_TEXT, second=body,
    )
    provenance = {row["group"]: row["marks"] for row in rows}
    assert provenance["project"] and provenance["personal"]
    after = await observe(env)
    assert gone(after, SRC) and gone(after, RET)
    assert CONTROL in after["search_all"] and CONTROL in after["mcp_search"] and CONTROL in after["model_input"]



@pytest.mark.asyncio
async def test_edit_during_recall_write_holds_graph_without_a_tombstone(env):
    from core.erasure_guard import GroupWriteRestricted, require_group_writable
    from core.late_writes import list_unsettled_writes, contain_known_unsettled_writes
    from core.settlement import settle_unsettled_write

    src = await seed_source(env)
    receipt = await read_source(env, src)
    gate = env.llm.hold_next("extract_nodes.extract_message")
    task = asyncio.create_task(retell(env, receipt, [src]))
    try:
        await asyncio.wait_for(gate.reached.wait(), 30)
        await env.q("MATCH (x:Episodic {uuid:$u}) SET x.content=$text",
                    u=src, text="Mira Venn changed the vault passphrase.")
    finally:
        gate.release.set()
        result = await task
    assert result["unconfirmed_writes"] == 1
    assert any("ReadReceiptStale" in warning for warning in result["warnings"])
    assert await env.q("MATCH (t:FractalErasure) RETURN t.event_id AS event") == []
    after = await observe(env)
    assert gone(after, RET)
    assert CONTROL in after["episodes"] and CONTROL in after["model_input"]
    attempts = [a for a in await list_unsettled_writes(env.graphiti) if a["group_id"] == P]
    assert len(attempts) == 1 and attempts[0]["episode_uuid"] and not attempts[0]["events"]
    await contain_known_unsettled_writes(env.graphiti)
    with pytest.raises(GroupWriteRestricted):
        await require_group_writable(env.graphiti, P)
    refused = await settle_unsettled_write(env.graphiti, kind="ingest", key=attempts[0]["key"],
                                          group_id=P, outcome="contained", decided_by="owner",
                                          episode_uuids=[attempts[0]["episode_uuid"]])
    assert refused["refused"] == "recall_containment_requires_erasure"
    assert gone(await observe(env), RET)


@pytest.mark.asyncio
@pytest.mark.parametrize("hard", [False, True], ids=["soft", "hard"])
async def test_lost_reply_after_cross_group_erasure_remains_held(env, monkeypatch, hard):
    from core.erasure_guard import GroupWriteRestricted, require_group_writable
    from core.late_writes import list_unsettled_writes

    src = await seed_source(env)
    receipt = await read_source(env, src)
    add_episode = env.graphiti.add_episode

    async def lose_reply_after_write(*args, **kwargs):
        await add_episode(*args, **kwargs)
        raise RuntimeError("Simulated lost Graphiti reply after commit")

    monkeypatch.setattr(env.graphiti, "add_episode", lose_reply_after_write)
    gate = env.llm.hold_next("extract_nodes.extract_message")
    task = asyncio.create_task(retell(env, receipt, [src]))
    try:
        await asyncio.wait_for(gate.reached.wait(), 30)
        await erase(env, src, hard=hard)
    finally:
        gate.release.set()
        result = await task
        monkeypatch.setattr(env.graphiti, "add_episode", add_episode)
    assert result["unconfirmed_writes"] == 1
    attempts = [a for a in await list_unsettled_writes(env.graphiti) if a["group_id"] == P]
    assert len(attempts) == 1 and attempts[0]["episode_uuid"] is None and attempts[0]["events"]
    with pytest.raises(GroupWriteRestricted):
        await require_group_writable(env.graphiti, P)
    after = await observe(env)
    assert gone(after, SRC) and gone(after, RET)
    assert CONTROL in after["episodes"] and CONTROL in after["model_input"]


@pytest.mark.asyncio
async def test_pending_receipt_write_is_hidden_before_upstream_reply(env, monkeypatch):
    from core.erasure_guard import GroupWriteRestricted, require_group_writable

    src = await seed_source(env)
    receipt = await read_source(env, src)
    add_episode = env.graphiti.add_episode
    written, release = asyncio.Event(), asyncio.Event()

    async def hold_reply_after_write(*args, **kwargs):
        result = await add_episode(*args, **kwargs)
        written.set()
        await release.wait()
        return result

    monkeypatch.setattr(env.graphiti, "add_episode", hold_reply_after_write)
    task = asyncio.create_task(retell(env, receipt, [src]))
    try:
        await asyncio.wait_for(written.wait(), 30)
        before = await observe(env, ask=False)
        assert RET not in before["search_all"] and RET not in before["mcp_search"]
        assert CONTROL in before["episodes"]
        with pytest.raises(GroupWriteRestricted):
            await require_group_writable(env.graphiti, P)
    finally:
        release.set()
        result = await task
        monkeypatch.setattr(env.graphiti, "add_episode", add_episode)
    assert result["completeness"] == "complete"
    await require_group_writable(env.graphiti, P)
    after = await observe(env)
    assert RET in after["model_input"] and CONTROL in after["model_input"]


@pytest.mark.asyncio
async def test_expired_receipt_during_write_cannot_finalize(env):
    src = await seed_source(env)
    receipt = await read_source(env, src)
    gate = env.llm.hold_next("extract_nodes.extract_message")
    task = asyncio.create_task(retell(env, receipt, [src]))
    try:
        await asyncio.wait_for(gate.reached.wait(), 30)
        await env.q("MATCH (r:FractalReadReceipt {receipt_id:$rid}) "
                    "SET r.expires_at=datetime()-duration('PT1S')",
                    rid=receipt["read_receipt"]["receipt_id"])
    finally:
        gate.release.set()
        result = await task
    assert result["unconfirmed_writes"] == 1
    assert any("ReadReceiptStale" in warning for warning in result["warnings"])
    assert gone(await observe(env), RET)



@pytest.mark.asyncio
@pytest.mark.parametrize("source_kind", ["episode", "cross_group_entity"])
@pytest.mark.parametrize("hard", [False, True], ids=["soft", "hard"])
async def test_unknown_receipt_write_holds_only_related_erasure_after_recovery(env, monkeypatch, neo4j_isolation,
                                                                               source_kind, hard):
    """Codex follow-up: two erasures, one lost reply, fresh connection, explicit settlement."""
    from graphiti_core import Graphiti
    from core.erasure_guard import GroupWriteRestricted, require_group_writable
    from core.erasure_scope import rebuild_area, scope_pending_erasures
    from core.late_writes import list_unsettled_writes
    from core.settlement import settle_unsettled_write
    from mcp_server import server
    from tests.support.recording_model import HashEmbedder, ZeroRanker

    src = await seed_source(env)
    await env.add("[[Weather Station]] reports MORNINGSKY near [[Hill Observatory]].", "experience")
    other = await env.uuid_of("MORNINGSKY")
    receipt = await read_source(env, src)
    selected, body = src, D_TEXT
    if source_kind == "cross_group_entity":
        assert (await retell(env, receipt, [src], group="personal"))["completeness"] == "complete"
        receipt = await mcp("memory.search_knowledge", query="Mira Venn vault secret phrase", group_id="personal")
        selected = next(row["uuid"] for row in receipt["items"] if row["kind"] == "Entity" and RET in row["text"])
        body = f"[[Quill Raven]] repeats the vault secret phrase {RET} for [[Glass Door]]."
    add_episode = env.graphiti.add_episode

    async def lose_reply(*args, **kwargs):
        await add_episode(*args, **kwargs)
        raise RuntimeError("Simulated lost Graphiti reply after commit")

    monkeypatch.setattr(env.graphiti, "add_episode", lose_reply)
    gate = env.llm.hold_next("extract_nodes.extract_message")
    task = asyncio.create_task(retell(env, receipt, [selected], text=body))
    try:
        await asyncio.wait_for(gate.reached.wait(), 30)
        claim = (await env.q("MATCH (c:FractalIngestClaim {group_id:$g,state:'PENDING'}) "
                             "WHERE c.recall_receipt_id IS NOT NULL "
                             "RETURN c.recall_source_ids AS ids, c.recall_scope_version AS version", g=P))[0]
        assert claim == {"ids": [selected], "version": 1}
        unrelated = await erase(env, other, hard=hard)
        related = await erase(env, src, hard=hard)
        await scope_pending_erasures(env.graphiti)
        assert (await rebuild_area(env.graphiti, unrelated["event_id"]))["held_for_recall_claim"] == 0
        attempt = next(a for a in await list_unsettled_writes(env.graphiti) if a["group_id"] == P)
        assert set(attempt["events"]) == {related["event_id"]}
        assert (await rebuild_area(env.graphiti, related["event_id"]))["held_for_recall_claim"] == 1
    finally:
        gate.release.set()
        result = await task
        monkeypatch.setattr(env.graphiti, "add_episode", add_episode)
    assert result["unconfirmed_writes"] == 1
    stored = await env.q("MATCH (e:Episodic {group_id:$g,content:$body}) RETURN e.uuid AS u", g=P, body=body)
    assert len(stored) == 1

    settings = neo4j_isolation
    fresh = Graphiti(settings.uri, settings.user, settings.password, llm_client=env.llm,
                     embedder=HashEmbedder(), cross_encoder=ZeroRanker())
    with monkeypatch.context() as patch:
        patch.setattr(env, "graphiti", fresh)
        patch.setattr(server, "_graphiti", fresh)
        try:
            async def statuses():
                rows = await env.q("MATCH (t:FractalErasure) RETURN t.event_id AS id,t.status AS status")
                return {row["id"]: row["status"] for row in rows}

            await scope_pending_erasures(fresh)
            await rebuild_area(fresh)
            before = await statuses()
            assert before[related["event_id"]] == "SCOPED"
            assert before[unrelated["event_id"]] == "RESOLVED"
            with pytest.raises(GroupWriteRestricted):
                await require_group_writable(fresh, P)
            attempt = next(a for a in await list_unsettled_writes(fresh) if a["group_id"] == P)
            assert attempt["episode_uuid"] is None and set(attempt["events"]) == {related["event_id"]}
            after = await observe(env)
            assert gone(after, SRC) and gone(after, RET)
            assert CONTROL in after["episodes"] and CONTROL in after["model_input"]

            decision = await settle_unsettled_write(
                fresh, kind="ingest", key=attempt["key"], group_id=P, outcome="contained",
                decided_by="owner", episode_uuids=[stored[0]["u"]],
            )
            assert decision["settled"], decision
            assert not any(a["key"] == attempt["key"] for a in await list_unsettled_writes(fresh))
            assert (await statuses())[unrelated["event_id"]] == "RESOLVED"
            wrong_marks = await env.q(
                "MATCH (n) WHERE any(m IN coalesce(n.erasure_quarantine,[]) WHERE m STARTS WITH $prefix) "
                "RETURN count(n) AS n", prefix=unrelated["event_id"] + ":")
            wrong_edges = await env.q(
                "MATCH ()-[r:RELATES_TO]->() WHERE any(m IN coalesce(r.erasure_quarantine,[]) "
                "WHERE m STARTS WITH $prefix) RETURN count(r) AS n", prefix=unrelated["event_id"] + ":")
            assert wrong_marks == [{"n": 0}] and wrong_edges == [{"n": 0}]
            after = await observe(env)
            assert gone(after, SRC) and gone(after, RET)
            assert CONTROL in after["episodes"] and CONTROL in after["model_input"]
        finally:
            await fresh.close()


@pytest.mark.asyncio
async def test_unrelated_event_cannot_settle_stale_receipt_and_missing_scope_fails_closed(env, monkeypatch):
    from core.erasure_scope import rebuild_area, scope_pending_erasures
    from core.late_writes import list_unsettled_writes
    from core.settlement import settle_unsettled_write

    src = await seed_source(env)
    await env.add("[[Weather Station]] reports MORNINGSKY near [[Hill Observatory]].", "experience")
    other = await env.uuid_of("MORNINGSKY")
    receipt = await read_source(env, src)
    gate = env.llm.hold_next("extract_nodes.extract_message")
    task = asyncio.create_task(retell(env, receipt, [src]))
    try:
        await asyncio.wait_for(gate.reached.wait(), 30)
        await env.q("MATCH (e:Episodic {uuid:$u}) SET e.content='Changed source'", u=src)
        unrelated = await erase(env, other, hard=False)
    finally:
        gate.release.set()
        result = await task
    assert result["unconfirmed_writes"] == 1
    await scope_pending_erasures(env.graphiti)
    assert (await rebuild_area(env.graphiti, unrelated["event_id"]))["held_for_recall_claim"] == 0
    attempt = next(a for a in await list_unsettled_writes(env.graphiti) if a["group_id"] == P)
    assert attempt["episode_uuid"] and attempt["events"] == []
    args = dict(kind="ingest", key=attempt["key"], group_id=P, outcome="contained",
                decided_by="owner", episode_uuids=[attempt["episode_uuid"]])
    refused = await settle_unsettled_write(env.graphiti, **args)
    assert refused["refused"] == "recall_containment_requires_erasure"

    # Simulate a legacy/incomplete footprint: no proof of independence is
    # available. Recovery holds the event but settlement must not invent marks.
    await env.q("MATCH (t:FractalErasure {event_id:$e}) REMOVE t.scope_relation_version", e=unrelated["event_id"])
    await scope_pending_erasures(env.graphiti)
    attempt = next(a for a in await list_unsettled_writes(env.graphiti) if a["group_id"] == P)
    assert set(attempt["events"]) == {unrelated["event_id"]}
    assert (await rebuild_area(env.graphiti, unrelated["event_id"]))["held_for_recall_claim"] == 1
    refused = await settle_unsettled_write(env.graphiti, **args)
    assert refused["refused"] == "recall_containment_uncertain"
    assert gone(await observe(env), RET)
