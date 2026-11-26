"""Writes whose outcome is unknown while an erasure happens (case 1 of the final review of #58).

``Graphiti.add_episode`` is one long call (previous episodes -> model -> database writes). The reply
can be lost after the database already changed: a timeout, a rate-limit error raised after the
commit, a dropped connection, a process that dies. If an erasure landed in the meantime, the
objects the call created or changed are unknown, so they cannot be marked and the group cannot be
declared safe.

Rules implemented here (all of them survive a process restart, because they are derived from
durable records, not from memory):

* every model-assisted write leaves a durable attempt record before it calls Graphiti: the ingest
  claim (``write_attempted_at`` + ``erasure_revision``) or, for chat, ``FractalChatWriteAttempt``;
* an attempt that is not settled and is older than an erasure (its snapshot revision < the
  erasure's revision) pauses model-assisted writes in its group (``require_group_writable``) and
  keeps that erasure's event open (``_close_event``) - automatic resumption is impossible;
* what is known (the episode UUID was recorded) is contained with ``mark_late_write``;
* what is unknown is settled only by an explicit decision with evidence
  (``core.settlement.settle_unsettled_write``): ``no_write`` (verified that nothing landed) or ``contained`` (the
  episodes that landed are named and are marked here). Retrying with ``add_episode(uuid=...)`` is
  not a way to complete it: in graphiti_core 0.29.3 that call reprocesses an existing episode node
  from the body passed now (new model calls, derived objects created again, stored content and
  name unchanged) and raises NodeNotFoundError for an unknown UUID (see
  tests/integration/test_ambiguous_late_write.py).
"""
from __future__ import annotations

from uuid import uuid4

from core.erasure_guard import (
    LOCK_ERASURE_CONTROL, UNSETTLED_CHAT, UNSETTLED_INGEST, ErasureFenceChanged, ensure_erasure_constraints,
)


async def _rows(graphiti, cypher: str, **params) -> list[dict]:
    return [dict(r) for r in (await graphiti.driver.execute_query(cypher, **params)).records]


# --- chat attempts ----------------------------------------------------------------------------

async def begin_chat_write_attempt(graphiti, *, group_id: str, expected_revision: int, persist_id: str,
                                   body_sha256: str | None = None) -> str:
    """Record, before add_episode, that this chat write is about to reach Graphiti.

    Admitted only at the revision the caller observed (the chat path is fenced globally); the
    record carries that revision, which is what makes an erasure that comes later "newer"."""
    await ensure_erasure_constraints(graphiti)
    attempt_id = str(uuid4())
    rows = await _rows(graphiti, LOCK_ERASURE_CONTROL + """
        WHERE coalesce(gate.revision,0) = $rev
        CREATE (a:FractalChatWriteAttempt {attempt_id:$id, group_id:$g, state:'OPEN', persist_id:$p, body_sha256:$sha,
                                           erasure_revision:coalesce(gate.revision,0), started_at:datetime()})
        RETURN a.attempt_id AS id""", rev=expected_revision, id=attempt_id, g=group_id, p=persist_id, sha=body_sha256)
    if len(rows) != 1:
        raise ErasureFenceChanged("chat write not admitted: the erasure revision changed")
    return attempt_id


async def record_chat_attempt_episode(graphiti, attempt_id: str, episode_uuid: str) -> None:
    await graphiti.driver.execute_query(
        "MATCH (a:FractalChatWriteAttempt {attempt_id:$id}) SET a.episode_uuid=$u", id=attempt_id, u=episode_uuid)


async def settle_chat_write_attempt(graphiti, attempt_id: str, settlement: str = "completed") -> None:
    await graphiti.driver.execute_query(
        "MATCH (a:FractalChatWriteAttempt {attempt_id:$id, state:'OPEN'}) "
        "SET a.state='SETTLED', a.settled_at=datetime(), a.settlement=$s", id=attempt_id, s=settlement)


# --- keeping erasure events open ----------------------------------------------------------------

async def reopen_for_unsettled_writes(graphiti) -> list[str]:
    """Reopen (RESOLVED included) and hold every erasure that an unsettled write crossed.

    Idempotent, one statement under the erasure control lock; called right after a write ends
    unconfirmed and again by the recovery sweep after a restart."""
    rows = await _rows(graphiti, LOCK_ERASURE_CONTROL + f"""
        MATCH (t:FractalErasure)
        OPTIONAL MATCH (c:FractalIngestClaim) WHERE c.group_id = t.group_id AND {UNSETTLED_INGEST}
          AND coalesce(c.erasure_revision,0) < t.revision
        WITH t, collect(DISTINCT c.group_id) AS g1
        OPTIONAL MATCH (a:FractalChatWriteAttempt) WHERE {UNSETTLED_CHAT} AND coalesce(a.erasure_revision,0) < t.revision
        WITH t, g1, collect(DISTINCT a.group_id) AS g2
        WITH t, g1 + g2 AS gs WHERE size(gs) > 0
        UNWIND gs AS g
        WITH t, collect(DISTINCT g) AS held
        SET t.blocked_groups = [x IN coalesce(t.blocked_groups,[]) WHERE NOT x IN held] + held,
            t.status = CASE WHEN t.status = 'PENDING_SCOPE' THEN 'PENDING_SCOPE' ELSE 'SCOPED' END,
            t.resolved_at = null, t.held_for_unsettled_write = true
        RETURN collect(t.event_id) AS events""")
    return rows[0]["events"] if rows else []


async def contain_known_unsettled_writes(graphiti) -> dict:
    """Recovery after a restart: attempts whose episode UUID was recorded are contained and settled."""
    from core.erasure_scope import mark_late_write
    contained = 0
    claims = await _rows(graphiti, f"""
        MATCH (c:FractalIngestClaim) WHERE {UNSETTLED_INGEST} AND c.episode_uuid IS NOT NULL
          AND EXISTS {{ MATCH (t:FractalErasure {{group_id:c.group_id}}) WHERE t.revision > coalesce(c.erasure_revision,0) }}
        RETURN c.claim_key AS k, c.group_id AS g, coalesce(c.erasure_revision,0) AS r, c.episode_uuid AS u""")
    for c in claims:
        await mark_late_write(graphiti, c["g"], c["r"], [c["u"]])
        await graphiti.driver.execute_query(
            "MATCH (c:FractalIngestClaim {claim_key:$k}) SET c.late_settled_at=datetime(), c.late_settlement='contained'",
            k=c["k"])
        contained += 1
    chats = await _rows(graphiti, f"""
        MATCH (a:FractalChatWriteAttempt) WHERE {UNSETTLED_CHAT} AND a.episode_uuid IS NOT NULL
          AND EXISTS {{ MATCH (t:FractalErasure) WHERE t.revision > coalesce(a.erasure_revision,0) }}
        RETURN a.attempt_id AS id, a.group_id AS g, coalesce(a.erasure_revision,0) AS r, a.episode_uuid AS u""")
    for a in chats:
        await mark_late_write(graphiti, a["g"], a["r"], [a["u"]], any_group=True)
        await settle_chat_write_attempt(graphiti, a["id"], "contained")
        contained += 1
    return {"contained": contained}


# --- explicit settlement ------------------------------------------------------------------------

async def list_unsettled_writes(graphiti) -> list[dict]:
    """Read-only diagnostics: attempts that block a group, and the erasures that make them matter."""
    ingest = await _rows(graphiti, f"""
        MATCH (c:FractalIngestClaim) WHERE {UNSETTLED_INGEST}
        OPTIONAL MATCH (t:FractalErasure {{group_id:c.group_id}}) WHERE t.revision > coalesce(c.erasure_revision,0)
        WITH c, collect(t.event_id) AS events WHERE size(events) > 0
        RETURN 'ingest' AS kind, c.claim_key AS key, c.group_id AS group_id, c.state AS state,
               c.episode_uuid AS episode_uuid, toString(c.write_attempted_at) AS attempted_at, events""")
    chat = await _rows(graphiti, f"""
        MATCH (a:FractalChatWriteAttempt) WHERE {UNSETTLED_CHAT}
        OPTIONAL MATCH (t:FractalErasure) WHERE t.revision > coalesce(a.erasure_revision,0)
        WITH a, collect(t.event_id) AS events WHERE size(events) > 0
        RETURN 'chat' AS kind, a.attempt_id AS key, a.group_id AS group_id, a.state AS state,
               a.episode_uuid AS episode_uuid, toString(a.started_at) AS attempted_at, events""")
    return ingest + chat


async def finish_chat_attempts(graphiti, attempts: list[dict] | None, *, completed: bool) -> None:
    """End of a chat persist pipeline (success or not). Never raises.

    An attempt whose episode UUID is known is contained (a no-op unless an erasure is newer than
    the write's snapshot) and settled. An attempt whose reply was lost (no UUID) stays OPEN: its
    objects are unknown, so the erasures it crossed are reopened and the group stays paused."""
    import logging
    if not attempts:
        return
    try:
        from core.erasure_scope import mark_late_write
        for a in attempts:
            if a.get("uuid"):
                await mark_late_write(graphiti, a["group"], a["revision"], [a["uuid"]], any_group=True)
                await settle_chat_write_attempt(graphiti, a["id"], "completed" if completed else "contained")
        await reopen_for_unsettled_writes(graphiti)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).error(
            "Could not settle chat write attempts; the durable attempts still pause the group")
