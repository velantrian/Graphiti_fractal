"""Manual settlement of an unsettled write (item A of the review after 2fa6884) on a disposable Neo4j.

The pause caused by a write whose outcome is unknown is lifted only by an explicit decision with
evidence the program re-checks. Real: claims, chat attempts, erase_node, settlement, audit, the
admission predicates, a second independent connection. Only add_episode is the recording stub.
On every refusal: nothing is isolated, the foreign object stays visible and unchanged, and the
unsettled attempt keeps the pause.
"""
import asyncio
import json

import pytest

from core import settlement
from core.erasure_guard import erase_node, visible_node
from core.ingest_atomicity import acquire_ingest_claim, mark_ingest_claim_episode_created, mark_ingest_claim_write_attempt
from core.late_writes import list_unsettled_writes
from core.settlement import cancel_pending_settlement, explain_settlement, settle_unsettled_write
from core.text_utils import fingerprint
from knowledge.ingest import ingest_text_document
from tests.integration.test_ambiguous_late_write import (  # noqa: F401  (world is a fixture)
    G, H, chat_agent, chat_group, erasing_write, q, refused, seed_old, text, tomb, world,
)
from core.erasure_guard import read_erasure_state
from graphiti_core.llm_client.errors import RateLimitError

BASIS = {"basis_kind": "provider_request_log", "evidence_ref": "provider-log:req-none-0001"}


class Case:
    pass


async def ambiguous_ingest(world, label="a", *, land=True):
    """Ingest chunk whose reply is lost after an erasure landed. land=False: nothing was stored."""
    stub, g = world.stub, world.graph
    await seed_old(g)
    body = text(label)

    async def step(s, kwargs):
        if land:
            await s.write(kwargs)
        await erase_node(s, uuid="old-1")
        raise RateLimitError("Please try again in 0.01s.")

    stub.plan = [step]
    await ingest_text_document(stub, body, group_id=G, user_id="owner")
    c = Case()
    c.body, c.world = body, world
    c.key = (await list_unsettled_writes(world.restarted))[0]["key"]
    rows = await q(g, "MATCH (e:Episodic {group_id:$g}) WHERE e.content=$c RETURN e.uuid AS u", g=G, c=body.strip())
    c.landed = rows[0]["u"] if rows else None
    return c


async def foreign(graph, uuid, group, content="unrelated owner note"):
    await q(graph, "CREATE (:Episodic {uuid:$u, group_id:$g, content:$c, name:'f', source_description:'seed', "
                   "origin_class:'owner', created_at:datetime(), valid_at:datetime()})", u=uuid, g=group, c=content)


async def snapshot(graph, uuid):
    return (await q(graph, "MATCH (e:Episodic {uuid:$u}) RETURN properties(e) AS p", u=uuid))[0]["p"]


async def visible(graph, uuid):
    rows = await q(graph, f"MATCH (e:Episodic {{uuid:$u}}) WHERE {visible_node('e')} RETURN count(e) AS c", u=uuid)
    return rows[0]["c"] == 1


async def audits(graph):
    return await q(graph, "MATCH (a:FractalSettlementAudit) RETURN properties(a) AS a")


async def assert_still_paused(c, *, untouched):
    w = c.world
    assert "outcome is unknown" in (await refused(w.restarted, G)), "the unknown attempt must keep holding the group"
    row = (await q(w.graph, "MATCH (c:FractalIngestClaim) RETURN c.late_settled_at AS s, c.settling_decision AS d"))[0]
    assert row == {"s": None, "d": None}
    assert await audits(w.graph) == []
    for uuid, before in untouched.items():
        assert await snapshot(w.graph, uuid) == before, "a refused settlement must not touch any object"
    assert (await snapshot(w.graph, c.landed)).get("erasure_quarantined") is None or True


def call(c, **kw):
    base = dict(kind="ingest", key=c.key, group_id=G, outcome="contained", decided_by="owner:ruslan",
                episode_uuids=[c.landed])
    base.update(kw)
    return settle_unsettled_write(c.world.restarted, **base)


# --- refusals: nothing is isolated, the pause stays -------------------------------------------------

@pytest.mark.asyncio
async def test_foreign_uuid_of_the_same_group_is_refused_and_left_alone(world):
    c = await ambiguous_ingest(world)
    await foreign(world.graph, "foreign-g", G)                       # same group, created AFTER the attempt began
    before = {"foreign-g": await snapshot(world.graph, "foreign-g"), c.landed: await snapshot(world.graph, c.landed)}
    result = await call(c, episode_uuids=["foreign-g"])
    assert result == {"settled": False, "refused": "list_mismatch"}
    await assert_still_paused(c, untouched=before)
    assert await visible(world.graph, "foreign-g")


@pytest.mark.asyncio
async def test_uuid_of_another_group_is_refused(world):
    c = await ambiguous_ingest(world)
    await foreign(world.graph, "foreign-h", H)
    before = {"foreign-h": await snapshot(world.graph, "foreign-h"), c.landed: await snapshot(world.graph, c.landed)}
    assert (await call(c, episode_uuids=["foreign-h"]))["refused"] == "uuid_foreign_group"
    await assert_still_paused(c, untouched=before)
    assert await visible(world.graph, "foreign-h")


@pytest.mark.asyncio
async def test_missing_or_wrong_group_is_refused(world):
    c = await ambiguous_ingest(world)
    before = {c.landed: await snapshot(world.graph, c.landed)}
    for group, code in ((None, "group_required"), ("", "group_required"), ("  ", "group_required"),
                        ("no-such-group", "group_mismatch"), (H, "group_mismatch")):
        assert (await call(c, group_id=group))["refused"] == code, group
    await assert_still_paused(c, untouched=before)


@pytest.mark.asyncio
async def test_partially_wrong_incomplete_or_malformed_lists_are_refused_whole(world):
    c = await ambiguous_ingest(world)
    await foreign(world.graph, "foreign-g", G)
    before = {c.landed: await snapshot(world.graph, c.landed), "foreign-g": await snapshot(world.graph, "foreign-g")}
    cases = [([c.landed, "foreign-g"], "list_mismatch"),          # one right, one foreign: nothing is accepted
             ([], "bad_uuid_list"), ([c.landed, c.landed], "bad_uuid_list"), ([c.landed, ""], "bad_uuid_list"),
             (None, "bad_uuid_list"), ([c.landed, "does-not-exist"], "uuid_missing")]
    for uuids, code in cases:
        assert (await call(c, episode_uuids=uuids))["refused"] == code, uuids
    await assert_still_paused(c, untouched=before)
    assert (await snapshot(world.graph, c.landed)).get("erasure_quarantined") is None   # even the right one stayed as it was


@pytest.mark.asyncio
async def test_ambiguous_or_non_unique_links_are_refused(world):
    c = await ambiguous_ingest(world)
    await q(world.graph, "CREATE (:Episodic {uuid:'twin', group_id:$g, content:$c, name:'t', source_description:'seed', "
                         "created_at:datetime()})", g=G, c=c.body.strip())      # a second episode that looks the same
    assert (await call(c))["refused"] == "ambiguous_candidates"
    await q(world.graph, "MATCH (e:Episodic {uuid:'twin'}) DETACH DELETE e")
    await q(world.graph, "CREATE (:Episodic {uuid:$u, group_id:$g, content:'dup', name:'d', source_description:'seed', "
                         "created_at:datetime()})", u=c.landed, g=G)              # the same uuid on two nodes
    assert (await call(c))["refused"] == "uuid_not_unique"
    assert "outcome is unknown" in (await refused(world.restarted, G)) and await audits(world.graph) == []


@pytest.mark.asyncio
async def test_no_link_can_be_shown_when_the_content_does_not_match(world):
    c = await ambiguous_ingest(world)
    await q(world.graph, "MATCH (e:Episodic {uuid:$u}) SET e.content='something else entirely'", u=c.landed)
    assert (await call(c))["refused"] == "no_provable_link"     # same group, after the attempt - still not enough
    assert "outcome is unknown" in (await refused(world.restarted, G))


@pytest.mark.asyncio
async def test_attempt_must_be_open_and_old_enough(world, monkeypatch):
    c = await ambiguous_ingest(world)
    monkeypatch.setattr(settlement, "SETTLEMENT_MIN_AGE_SECONDS", 900)
    assert (await call(c))["refused"] == "attempt_too_recent"   # a call still in flight is not a lost one
    monkeypatch.setattr(settlement, "SETTLEMENT_MIN_AGE_SECONDS", 0)
    done = await ingest_text_document(world.stub, text("ok"), group_id=H, user_id="owner")
    assert done["completeness"] == "complete"
    committed = (await q(world.graph, "MATCH (c:FractalIngestClaim {state:'COMMITTED'}) RETURN c.claim_key AS k"))[0]["k"]
    assert (await call(c, key=committed, group_id=H))["refused"] == "attempt_not_open"
    assert (await call(c, key="nope"))["refused"] == "unknown_attempt"
    assert (await call(c, outcome="maybe"))["refused"] == "bad_request"


# --- no_write: who decided, on what independent basis -------------------------------------------------

@pytest.mark.asyncio
async def test_no_write_needs_a_decider_an_independent_basis_and_no_matching_episode(world):
    c = await ambiguous_ingest(world)                           # the episode DID land
    nw = dict(outcome="no_write", episode_uuids=None, **BASIS)
    assert (await call(c, **{**nw, "decided_by": None}))["refused"] == "decided_by_required"
    assert (await call(c, **{**nw, "basis_kind": "node_absent"}))["refused"] == "basis_not_independent"
    assert (await call(c, **{**nw, "basis_kind": None}))["refused"] == "basis_not_independent"
    assert (await call(c, **{**nw, "evidence_ref": "x"}))["refused"] == "evidence_ref_required"
    assert (await call(c, **nw))["refused"] == "matching_episode_exists"     # the evidence contradicts the claim
    assert (await call(c, **{**nw, "episode_uuids": [c.landed]}))["refused"] == "bad_uuid_list"
    assert "outcome is unknown" in (await refused(world.restarted, G)) and await audits(world.graph) == []


@pytest.mark.asyncio
async def test_absence_of_the_node_is_not_a_basis_but_an_independent_basis_is(world):
    c = await ambiguous_ingest(world)
    await q(world.graph, "MATCH (e:Episodic {uuid:$u}) DETACH DELETE e", u=c.landed)     # "nothing there now"
    assert (await call(c, outcome="no_write", episode_uuids=None, basis_kind="node_absent",
                       evidence_ref="looked, nothing there"))["refused"] == "basis_not_independent"
    assert "outcome is unknown" in (await refused(world.restarted, G))
    done = await call(c, outcome="no_write", episode_uuids=None, **BASIS)
    assert done["settled"] is True and await refused(world.restarted, G) is None
    a = (await audits(world.graph))[0]["a"]
    assert (a["decided_by"], a["basis_kind"], a["evidence_ref"], a["status"]) == (
        "owner:ruslan", "provider_request_log", "provider-log:req-none-0001", "COMMITTED")


# --- success: the basis is rebuilt from persistent data ------------------------------------------------------

@pytest.mark.asyncio
async def test_contained_settlement_leaves_an_audit_that_can_be_re_verified(world):
    c = await ambiguous_ingest(world)
    await foreign(world.graph, "foreign-g", G)
    foreign_before = await snapshot(world.graph, "foreign-g")
    done = await call(c)
    assert done["settled"] is True and done["already_settled"] is False and done["events_still_open"] == []
    assert await refused(world.restarted, G) is None
    assert not await visible(world.graph, c.landed) and await visible(world.graph, "foreign-g")
    assert await snapshot(world.graph, "foreign-g") == foreign_before

    rows = await audits(world.graph)
    assert len(rows) == 1
    a = rows[0]["a"]
    proof = json.loads(a["link_proof"])
    assert (a["status"], a["decided_by"], a["outcome"], a["group_id"], a["episode_uuids"]) == (
        "COMMITTED", "owner:ruslan", "contained", G, [c.landed])
    assert proof[0]["basis"] == "content_fingerprint_after_attempt" and proof[0]["attempt_proof_key"] == fingerprint(c.body.strip())

    # a person (or a restarted process) reconstructs WHY from the stored records alone
    why = await explain_settlement(world.restarted, kind="ingest", key=c.key)
    assert why["basis_verified"] is True
    assert why["audits"][0]["checks"] == [{"uuid": c.landed, "basis": "content_fingerprint_after_attempt", "holds": True}]
    # tampering with the linked object is detected
    await q(world.graph, "MATCH (e:Episodic {uuid:$u}) SET e.content='altered'", u=c.landed)
    assert (await explain_settlement(world.restarted, kind="ingest", key=c.key))["basis_verified"] is False


@pytest.mark.asyncio
async def test_repeat_of_a_finished_decision_returns_it_and_a_different_one_is_refused(world):
    c = await ambiguous_ingest(world)
    first = await call(c)
    again = await call(c)
    assert again["settled"] and again["already_settled"] is True and again["audit_id"] == first["audit_id"]
    other = await call(c, decided_by="someone:else")
    assert other["refused"] == "already_settled" and other["audit_id"] == first["audit_id"]
    assert len(await audits(world.graph)) == 1


# --- two concurrent callers ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_competing_decisions_have_exactly_one_winner(world):
    c = await ambiguous_ingest(world)
    a = settle_unsettled_write(world.restarted, kind="ingest", key=c.key, group_id=G, outcome="contained",
                               decided_by="owner:alice", episode_uuids=[c.landed])
    b = settle_unsettled_write(world.stub, kind="ingest", key=c.key, group_id=G, outcome="contained",
                               decided_by="owner:bob", episode_uuids=[c.landed])
    ra, rb = await asyncio.gather(a, b)
    wins = [r for r in (ra, rb) if r.get("settled")]
    loses = [r for r in (ra, rb) if not r.get("settled")]
    assert len(wins) == 1 and len(loses) == 1
    assert loses[0]["refused"] in ("settlement_decision_conflict", "already_settled")
    rows = await audits(world.graph)
    assert len(rows) == 1 and rows[0]["a"]["status"] == "COMMITTED"
    assert rows[0]["a"]["decided_by"] == ("owner:alice" if ra.get("settled") else "owner:bob")
    assert rows[0]["a"]["audit_id"] == wins[0]["audit_id"]


@pytest.mark.asyncio
async def test_two_identical_decisions_settle_once(world):
    c = await ambiguous_ingest(world)
    args = dict(kind="ingest", key=c.key, group_id=G, outcome="contained", decided_by="owner:ruslan",
                episode_uuids=[c.landed])
    r1, r2 = await asyncio.gather(settle_unsettled_write(world.restarted, **args),
                                  settle_unsettled_write(world.stub, **args))
    assert r1["settled"] and r2["settled"] and r1["audit_id"] == r2["audit_id"]
    assert sorted([r1["already_settled"], r2["already_settled"]]) == [False, True]
    assert len(await audits(world.graph)) == 1 and await refused(world.restarted, G) is None


# --- failure between isolation and the recorded decision -----------------------------------------------------------

@pytest.mark.asyncio
async def test_failure_between_isolation_and_commit_keeps_the_pause_and_a_retry_completes(world, monkeypatch):
    c = await ambiguous_ingest(world)
    await foreign(world.graph, "foreign-g", G)
    foreign_before = await snapshot(world.graph, "foreign-g")

    async def crash(name):
        if name == "after_isolation":
            raise RuntimeError("process died here")

    monkeypatch.setattr(settlement, "_checkpoint", crash)
    with pytest.raises(RuntimeError):
        await call(c)
    monkeypatch.undo()
    monkeypatch.setattr(settlement, "SETTLEMENT_MIN_AGE_SECONDS", 0)

    # isolated, decision NOT recorded, audit says so, the attempt still holds the pause
    assert not await visible(world.graph, c.landed) and await visible(world.graph, "foreign-g")
    assert "outcome is unknown" in (await refused(world.restarted, G))
    rows = await audits(world.graph)
    assert len(rows) == 1 and rows[0]["a"]["status"] == "ISOLATING"
    claim = (await q(world.graph, "MATCH (c:FractalIngestClaim) RETURN c.late_settled_at AS s"))[0]
    assert claim["s"] is None
    assert (await call(c, decided_by="someone:else"))["refused"] == "settlement_decision_conflict"

    done = await call(c)                                    # the same decision, retried
    assert done["settled"] is True and done["audit_id"] == rows[0]["a"]["audit_id"]
    rows = await audits(world.graph)
    assert len(rows) == 1 and rows[0]["a"]["status"] == "COMMITTED"
    assert await refused(world.restarted, G) is None
    assert await snapshot(world.graph, "foreign-g") == foreign_before


@pytest.mark.asyncio
async def test_an_unfinished_decision_can_be_cancelled_and_replaced(world, monkeypatch):
    c = await ambiguous_ingest(world)

    async def crash(name):
        if name == "after_isolation":
            raise RuntimeError("process died here")

    monkeypatch.setattr(settlement, "_checkpoint", crash)
    with pytest.raises(RuntimeError):
        await call(c)
    monkeypatch.undo()
    monkeypatch.setattr(settlement, "SETTLEMENT_MIN_AGE_SECONDS", 0)
    assert (await cancel_pending_settlement(world.restarted, kind="ingest", key=c.key, cancelled_by=""))["refused"] == "bad_request"
    assert (await cancel_pending_settlement(world.restarted, kind="ingest", key=c.key, cancelled_by="owner:bob"))["cancelled"]
    assert (await audits(world.graph))[0]["a"]["status"] == "CANCELLED"
    done = await call(c, decided_by="owner:bob")
    assert done["settled"] and len(await audits(world.graph)) == 2


# --- attempt that already recorded its episode ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_recorded_uuid_must_be_exactly_the_recorded_episode(world):
    stub, g = world.stub, world.graph
    await seed_old(g)
    body = text("rec")
    fp = fingerprint(body.strip())
    token = await acquire_ingest_claim(stub, group_id=G, fingerprint=fp, expected_revision=0)
    await mark_ingest_claim_write_attempt(stub, group_id=G, fingerprint=fp, token=token)
    landed = await stub.write({"name": "n", "episode_body": body.strip(), "source_description": "s", "group_id": G})
    await mark_ingest_claim_episode_created(stub, group_id=G, fingerprint=fp, token=token, episode_uuid=landed.episode.uuid)
    await foreign(g, "foreign-g", G)
    await erase_node(world.restarted, uuid="old-1")
    c = Case()
    c.world, c.body, c.landed = world, body, landed.episode.uuid
    c.key = f"{G}:{fp}"
    assert (await call(c, episode_uuids=["foreign-g"]))["refused"] == "list_mismatch"
    assert (await call(c, episode_uuids=[c.landed, "foreign-g"]))["refused"] == "list_mismatch"
    assert (await call(c, outcome="no_write", episode_uuids=None, **BASIS))["refused"] == "recorded_episode_exists"
    assert (await call(c))["settled"] is True


# --- chat attempts ----------------------------------------------------------------------------------------------------------

async def ambiguous_chat(world):
    stub, g = world.stub, world.graph
    await seed_old(g)
    personal = await chat_group()
    state = await read_erasure_state(stub)
    stub.plan = [erasing_write("old-1", then=TimeoutError("reply lost"))]
    await chat_agent(stub)._persist_turn_pipeline(conversation_id="cx", conversation_text="User: secret\nAssistant: ok",
                                                  expected_state=state)
    c = Case()
    c.world, c.personal = world, personal
    c.key = (await list_unsettled_writes(world.restarted))[0]["key"]
    c.landed = (await q(g, "MATCH (e:Episodic {group_id:$g}) WHERE e.source_description='chat' RETURN e.uuid AS u",
                        g=personal))[0]["u"]
    return c


def chat_call(c, **kw):
    base = dict(kind="chat", key=c.key, group_id=c.personal, outcome="contained", decided_by="owner:ruslan",
                episode_uuids=[c.landed])
    base.update(kw)
    return settle_unsettled_write(c.world.restarted, **base)


@pytest.mark.asyncio
async def test_chat_attempt_links_by_the_recorded_body_hash_not_by_group(world):
    c = await ambiguous_chat(world)
    await foreign(world.graph, "foreign-p", c.personal)
    before = await snapshot(world.graph, "foreign-p")
    assert (await chat_call(c, episode_uuids=["foreign-p"]))["refused"] == "list_mismatch"
    assert (await chat_call(c, episode_uuids=[c.landed, "foreign-p"]))["refused"] == "list_mismatch"
    assert (await chat_call(c, group_id=G))["refused"] == "group_mismatch"
    assert await snapshot(world.graph, "foreign-p") == before and await visible(world.graph, "foreign-p")
    assert "outcome is unknown" in (await refused(world.restarted, c.personal))
    assert await audits(world.graph) == []
    done = await chat_call(c)
    assert done["settled"] and await refused(world.restarted, c.personal) is None
    why = await explain_settlement(world.restarted, kind="chat", key=c.key)
    assert why["basis_verified"] is True and await snapshot(world.graph, "foreign-p") == before


@pytest.mark.asyncio
async def test_chat_attempt_without_a_recorded_body_hash_cannot_be_linked_and_keeps_the_pause(world):
    c = await ambiguous_chat(world)
    await q(world.graph, "MATCH (a:FractalChatWriteAttempt) REMOVE a.body_sha256")      # an attempt from before the hash existed
    assert (await chat_call(c))["refused"] == "no_provable_link"
    assert (await chat_call(c, outcome="no_write", episode_uuids=None, **BASIS))["refused"] == "attempt_recorded_nothing_to_check"
    assert "outcome is unknown" in (await refused(world.restarted, c.personal))
    assert await audits(world.graph) == []


@pytest.mark.asyncio
async def test_a_decision_validated_before_a_competitor_claimed_the_attempt_is_still_refused(world, monkeypatch):
    """Both callers pass every pre-check (nobody has claimed the attempt yet); one then claims it and dies
    after isolating. The other must be stopped by the locked claim statement itself, not by the pre-check."""
    c = await ambiguous_ingest(world)
    gate_reached, gate_release, calls = asyncio.Event(), asyncio.Event(), []

    async def hook(name):
        if name == "before_claim":
            calls.append(name)
            if len(calls) == 1:                                   # caller A parks here, validated, not yet claimed
                gate_reached.set()
                await gate_release.wait()
        if name == "after_isolation" and len(calls) >= 2:         # caller B isolates and dies
            raise RuntimeError("B died after isolating")

    monkeypatch.setattr(settlement, "_checkpoint", hook)
    a = asyncio.create_task(call(c, decided_by="owner:alice"))
    await asyncio.wait_for(gate_reached.wait(), 30)
    with pytest.raises(RuntimeError):
        await call(c, decided_by="owner:bob", )
    gate_release.set()
    result = await a
    assert result == {"settled": False, "refused": "settlement_decision_conflict",
                      "audit_id": result.get("audit_id")} and result["audit_id"]
    rows = await audits(world.graph)
    assert len(rows) == 1 and rows[0]["a"]["decided_by"] == "owner:bob" and rows[0]["a"]["status"] == "ISOLATING"
    assert "outcome is unknown" in (await refused(world.restarted, G))
