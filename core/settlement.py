"""Manual settlement of an unsettled write (the only way to lift a pause caused by an unknown outcome).

A pause caused by a write whose outcome is unknown (core/late_writes.py) is lifted by a person,
never automatically, and only on evidence that the program can re-check:

``contained``  every UUID must be provably linked to THIS attempt:
    * attempt with a recorded episode UUID: the list must be exactly [that UUID];
    * attempt without one: the group alone proves nothing. The episode is linked only by what the
      attempt itself recorded before the call - the claim fingerprint (ingest) / the SHA-256 of the
      body (chat) - matched against the episode content, an episode created after the attempt
      began, in the attempt's group, not claimed by another attempt. Exactly one such candidate
      must exist (none: no link can be shown; several: ambiguous) and the list must equal it.
    If no link can be established the pause stays. Nothing is isolated on a refusal.
``no_write``   needs who decided (``decided_by``) and an independent basis (``basis_kind`` from a
    fixed list + ``evidence_ref``). "The node is absent now" is not a basis, and the decision is
    refused while any provable candidate exists, while the attempt has a recorded UUID, and for an
    attempt that recorded nothing to look for (legacy chat attempt).
Both: explicit group that matches the attempt, attempt in a state that can still be settled, and
old enough that a call still in flight is not mistaken for a lost one (SETTLEMENT_MIN_AGE_SECONDS).

Consistency (isolation, decision, audit) across failure, retry and concurrent callers:
  1. ONE statement under the erasure control lock re-verifies the attempt and the exact objects
     (group, content), isolates the episodes, writes the audit node (status ISOLATING) and claims
     the attempt for this decision (``settling_decision``). A different decision loses here.
  2. exposure marks (``mark_late_write``), idempotent.
  3. ONE statement under the lock settles the attempt and marks the audit COMMITTED.
A failure between 1 and 3 leaves the attempt unsettled (the pause holds), the audit ISOLATING and
the episodes isolated; repeating the same decision resumes, a different one is refused until
``cancel_pending_settlement``. Repeating a finished decision returns it (``already_settled``).
``explain_settlement`` rebuilds the basis from persistent data and re-checks it.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from core.erasure_guard import LOCK_ERASURE_CONTROL, ingest_erasure_relation, ingest_crosses_erasure
from core.late_writes import _rows
from core.text_utils import fingerprint

SETTLEMENT_MIN_AGE_SECONDS = 900
NO_WRITE_BASES = ("provider_request_log", "graphiti_transaction_log", "operator_attestation")


async def _checkpoint(name: str) -> None:  # test hook: fail/interleave between settlement steps
    return None


def _refuse(code: str, **extra) -> dict:
    return {"settled": False, "refused": code, **extra}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decision_hash(kind, key, group_id, outcome, decided_by, basis_kind, evidence_ref, uuids) -> str:
    return _sha(json.dumps([kind, key, group_id, outcome, decided_by, basis_kind or "", evidence_ref or "",
                            sorted(uuids or [])], ensure_ascii=True))


_ATTEMPT = {
    "ingest": """MATCH (x:FractalIngestClaim {claim_key:$k})
        RETURN x.group_id AS group_id, x.state AS state, x.fingerprint AS proof_key, x.episode_uuid AS recorded,
               toString(x.write_attempted_at) AS since, x.write_attempted_at IS NOT NULL AS attempted,
               x.late_settled_at IS NOT NULL AS settled, x.settling_audit AS audit, x.settling_decision AS dh,
               coalesce(x.erasure_revision,0) AS rev,
               x.recall_receipt_id AS recall_receipt_id,
               coalesce(x.recall_receipt_revision,x.erasure_revision,0) AS recall_revision,
               x.write_attempted_at < datetime() - duration({seconds:$age}) AS old_enough""",
    "chat": """MATCH (x:FractalChatWriteAttempt {attempt_id:$k})
        RETURN x.group_id AS group_id, x.state AS state, x.body_sha256 AS proof_key, x.episode_uuid AS recorded,
               toString(x.started_at) AS since, true AS attempted,
               x.state <> 'OPEN' AS settled, x.settling_audit AS audit, x.settling_decision AS dh,
               coalesce(x.erasure_revision,0) AS rev,
               x.started_at < datetime() - duration({seconds:$age}) AS old_enough""",
}
_OPEN_INGEST = ("x.state IN ['PENDING','RECONCILIATION_REQUIRED'] AND x.write_attempted_at IS NOT NULL "
                "AND x.late_settled_at IS NULL AND x.write_attempted_at < datetime() - duration({seconds:$age})")
_OPEN_CHAT = "x.state = 'OPEN' AND x.started_at < datetime() - duration({seconds:$age})"
_MATCH = {"ingest": ("(x:FractalIngestClaim {claim_key:$key})", _OPEN_INGEST),
          "chat": ("(x:FractalChatWriteAttempt {attempt_id:$key})", _OPEN_CHAT)}


async def _load(graphiti, kind: str, key: str) -> dict | None:
    rows = await _rows(graphiti, _ATTEMPT[kind], k=key, age=SETTLEMENT_MIN_AGE_SECONDS)
    if len(rows) != 1:
        return None
    a = rows[0]
    a["valid_state"] = (a["state"] in ("PENDING", "RECONCILIATION_REQUIRED") and a["attempted"] and not a["settled"]
                        if kind == "ingest" else a["state"] == "OPEN")
    return a


async def _candidates(graphiti, kind: str, key: str, att: dict) -> list[dict]:
    """Episodes provably linked to the attempt by what the attempt recorded itself."""
    if not att["proof_key"] or not att["since"]:
        return []
    rows = await _rows(graphiti, """
        MATCH (e:Episodic {group_id:$g}) WHERE e.created_at >= datetime($since)
          AND NOT EXISTS { MATCH (o:FractalIngestClaim) WHERE o.episode_uuid = e.uuid AND o.claim_key <> $own_key }
          AND NOT EXISTS { MATCH (o:FractalChatWriteAttempt) WHERE o.episode_uuid = e.uuid AND o.attempt_id <> $own_key }
        RETURN e.uuid AS uuid, coalesce(e.content,'') AS content, toString(e.created_at) AS created_at""",
        g=att["group_id"], since=att["since"], own_key=key)
    out = []
    for r in rows:
        text = r["content"]
        linked = (fingerprint(text) == att["proof_key"] or fingerprint(text.strip()) == att["proof_key"]
                  if kind == "ingest" else _sha(text) == att["proof_key"])
        if linked:
            out.append(r)
    return out


async def _validate_contained(graphiti, kind, key, group_id, uuids, att):
    if (not isinstance(uuids, list) or not uuids or any(not isinstance(u, str) or not u for u in uuids)
            or len(set(uuids)) != len(uuids)):
        return _refuse("bad_uuid_list"), None
    nodes = {}
    for u in uuids:
        rows = await _rows(graphiti, "MATCH (e:Episodic {uuid:$u}) RETURN e.group_id AS g, coalesce(e.content,'') AS c, "
                                     "toString(e.created_at) AS ts", u=u)
        if not rows:
            return _refuse("uuid_missing"), None
        if len(rows) > 1:
            return _refuse("uuid_not_unique"), None
        if rows[0]["g"] != group_id:
            return _refuse("uuid_foreign_group"), None
        nodes[u] = rows[0]
    if att["recorded"]:
        expected, basis = {att["recorded"]}, "recorded_uuid"
    else:
        cands = await _candidates(graphiti, kind, key, att)
        if not cands:
            return _refuse("no_provable_link"), None
        if len(cands) > 1:
            return _refuse("ambiguous_candidates", candidates=len(cands)), None
        expected, basis = {cands[0]["uuid"]}, "content_fingerprint_after_attempt"
    if set(uuids) != expected:
        return _refuse("list_mismatch"), None
    proof = [{"uuid": u, "basis": basis, "content_sha256": _sha(nodes[u]["c"]), "created_at": nodes[u]["ts"],
              "attempt_proof_key": att["proof_key"], "attempt_since": att["since"]} for u in uuids]
    return None, {"proof": proof, "contents": {u: nodes[u]["c"] for u in uuids}}


async def settle_unsettled_write(graphiti, *, kind: str, key: str, group_id: str | None, outcome: str,
                                 decided_by: str | None, episode_uuids: list[str] | None = None,
                                 basis_kind: str | None = None, evidence_ref: str | None = None) -> dict:
    if kind not in ("ingest", "chat") or outcome not in ("no_write", "contained"):
        return _refuse("bad_request")
    if not isinstance(group_id, str) or not group_id.strip():
        return _refuse("group_required")
    if not isinstance(decided_by, str) or not decided_by.strip() or len(decided_by) > 200:
        return _refuse("decided_by_required")
    uuids = list(episode_uuids or [])
    dh = decision_hash(kind, key, group_id, outcome, decided_by, basis_kind, evidence_ref, uuids)
    att = await _load(graphiti, kind, key)
    if att is None:
        return _refuse("unknown_attempt")
    if att["group_id"] != group_id:
        return _refuse("group_mismatch")
    if att["settled"]:                                      # a repeat of the finished decision is not an error
        if att["dh"] == dh:
            return {"settled": True, "already_settled": True, "outcome": outcome, "audit_id": att["audit"],
                    "events_still_open": []}
        return _refuse("already_settled", audit_id=att["audit"])
    if not att["valid_state"]:
        return _refuse("attempt_not_open", state=att["state"])
    if att["dh"] and att["dh"] != dh:
        return _refuse("settlement_decision_conflict", audit_id=att["audit"])
    if not att["old_enough"]:
        return _refuse("attempt_too_recent", min_age_seconds=SETTLEMENT_MIN_AGE_SECONDS)

    contents, proof = {}, []
    if outcome == "contained":
        refusal, found = await _validate_contained(graphiti, kind, key, group_id, uuids, att)
        if refusal:
            return refusal
        contents, proof = found["contents"], found["proof"]
        if att.get("recall_receipt_id") is not None:
            # A stale edit is not an erasure. Existing event-based rebuild
            # cannot contain its graph until a real owner-authorized erasure
            # provides a durable scope, so retain the claim rather than fake it.
            events = await _rows(graphiti, f"""
                MATCH (c:FractalIngestClaim {{claim_key:$k}})
                MATCH (t:FractalErasure) WHERE {ingest_crosses_erasure()}
                WITH t, {ingest_erasure_relation()} AS relation
                RETURN count(t) AS n,
                       count(CASE WHEN relation='UNKNOWN' OR coalesce(t.status,'PENDING_SCOPE')='PENDING_SCOPE'
                                  THEN 1 END) AS uncertain""", k=key)
            if events[0]["uncertain"]:
                return _refuse("recall_containment_uncertain")
            if not events[0]["n"]:
                return _refuse("recall_containment_requires_erasure")
    else:
        if uuids:
            return _refuse("bad_uuid_list")
        if basis_kind not in NO_WRITE_BASES:
            return _refuse("basis_not_independent", allowed=list(NO_WRITE_BASES))
        if not isinstance(evidence_ref, str) or len(evidence_ref.strip()) < 8 or len(evidence_ref) > 500:
            return _refuse("evidence_ref_required")
        if att["recorded"]:
            return _refuse("recorded_episode_exists")
        if not att["proof_key"]:
            return _refuse("attempt_recorded_nothing_to_check")
        if await _candidates(graphiti, kind, key, att):
            return _refuse("matching_episode_exists")

    await _checkpoint("before_claim")
    match, open_where = _MATCH[kind]
    rows = await _rows(graphiti, LOCK_ERASURE_CONTROL + f"""
        MATCH {match}
        WHERE x.group_id=$g AND {open_where} AND (x.settling_decision IS NULL OR x.settling_decision=$dh)
        OPTIONAL MATCH (e:Episodic) WHERE e.uuid IN $uuids
        WITH x, collect(e) AS eps
        WHERE size(eps) = size($uuids) AND all(e IN eps WHERE e.group_id=$g AND coalesce(e.content,'') = $contents[e.uuid])
        FOREACH (e IN eps | SET e.erasure_quarantined = true)
        WITH x, coalesce(x.settling_audit, $audit_id) AS aid
        MERGE (a:FractalSettlementAudit {{audit_id:aid}})
          ON CREATE SET a.kind=$kind, a.attempt_key=$key, a.group_id=$g, a.outcome=$outcome, a.decided_by=$by,
              a.basis_kind=$basis, a.evidence_ref=$ref, a.episode_uuids=$uuids, a.link_proof=$proof,
              a.decision_hash=$dh, a.status='ISOLATING', a.created_at=datetime(),
              a.attempt_erasure_revision=coalesce(x.erasure_revision,0)
        SET x.settling_audit=aid, x.settling_decision=$dh
        RETURN aid AS audit_id""", key=key, g=group_id, age=SETTLEMENT_MIN_AGE_SECONDS, dh=dh, uuids=uuids,
        contents=contents, audit_id=str(uuid4()), kind=kind, outcome=outcome, by=decided_by.strip(),
        basis=basis_kind, ref=evidence_ref, proof=json.dumps(proof, sort_keys=True))
    if len(rows) != 1:
        now = await _load(graphiti, kind, key)
        if now and now["settled"] and now["dh"] == dh:
            return {"settled": True, "already_settled": True, "outcome": outcome, "audit_id": now["audit"],
                    "events_still_open": []}
        if now and now["dh"] and now["dh"] != dh:
            return _refuse("settlement_decision_conflict", audit_id=now["audit"])
        return _refuse("changed_during_settlement")
    audit_id = rows[0]["audit_id"]

    marked_events = []
    receipt_guard = ""
    if outcome == "contained":
        from core.erasure_scope import mark_late_write
        receipt = att.get("recall_receipt_id") is not None
        marked = await mark_late_write(graphiti, group_id, att["rev"], uuids,
                                       any_group=(kind == "chat"), ingest_claim_key=key if kind == "ingest" else None)
        marked_events = marked["events"]
        if receipt:
            if not marked_events:
                return _refuse("recall_containment_requires_erasure")
            # A new erasure or incomplete scope can appear between marking and
            # settling. Re-evaluate the exact same relation under the commit lock.
            # A retry may add marks; it may never lift an unresolved hold.
            receipt_guard = f"""AND NOT EXISTS {{
                MATCH (t:FractalErasure) WHERE {ingest_crosses_erasure("x", "t")}
                AND ({ingest_erasure_relation("x", "t")} <> 'RELATED'
                     OR coalesce(t.status,'PENDING_SCOPE')='PENDING_SCOPE'
                     OR NOT t.event_id IN $marked_events)
            }}"""
    await _checkpoint("after_isolation")

    done = await _rows(graphiti, LOCK_ERASURE_CONTROL + f"""
        MATCH {match} WHERE x.settling_audit=$aid AND x.settling_decision=$dh
          AND coalesce(x.late_settled_at, null) IS NULL AND coalesce(x.state,'') <> 'SETTLED'
          {receipt_guard}
        SET x.late_settled_at=datetime(), x.late_settlement=$outcome, x.settled_by=$by
            {", x.state='SETTLED', x.settled_at=datetime()" if kind == "chat" else ""}
        WITH x MATCH (a:FractalSettlementAudit {{audit_id:$aid}})
        SET a.status='COMMITTED', a.committed_at=datetime()
        RETURN a.audit_id AS id""", key=key, aid=audit_id, dh=dh, outcome=outcome, by=decided_by.strip(),
        marked_events=marked_events)
    already = len(done) != 1
    if already:
        now = await _load(graphiti, kind, key)
        if not (now and now["settled"] and now["dh"] == dh):
            return _refuse("changed_during_settlement")
    from core.erasure_scope import _close_event
    still_open = []
    for ev in await _rows(graphiti, "MATCH (t:FractalErasure) WHERE t.status = 'SCOPED' RETURN t.event_id AS e"):
        if await _close_event(graphiti, ev["e"], ev["e"] + ":") != "RESOLVED":
            still_open.append(ev["e"])
    return {"settled": True, "already_settled": already, "outcome": outcome, "audit_id": audit_id,
            "events_still_open": still_open}


async def cancel_pending_settlement(graphiti, *, kind: str, key: str, cancelled_by: str) -> dict:
    """Release an attempt claimed by a decision that never finished. Isolation already done stays."""
    if kind not in ("ingest", "chat") or not isinstance(cancelled_by, str) or not cancelled_by.strip():
        return _refuse("bad_request")
    match, open_where = _MATCH[kind]
    rows = await _rows(graphiti, LOCK_ERASURE_CONTROL + f"""
        MATCH {match} WHERE x.settling_audit IS NOT NULL AND {open_where}
        WITH x, x.settling_audit AS aid
        MATCH (a:FractalSettlementAudit {{audit_id:aid, status:'ISOLATING'}})
        SET a.status='CANCELLED', a.cancelled_by=$by, a.cancelled_at=datetime(),
            x.settling_audit=null, x.settling_decision=null
        RETURN aid""", key=key, age=0, by=cancelled_by.strip())
    return {"cancelled": len(rows) == 1}


async def explain_settlement(graphiti, *, kind: str, key: str) -> dict:
    """Rebuild the basis of a decision from persistent data and re-check it (read-only)."""
    audits = await _rows(graphiti, "MATCH (a:FractalSettlementAudit {kind:$k, attempt_key:$key}) "
                                   "RETURN properties(a) AS a ORDER BY a.created_at", k=kind, key=key)
    att = await _load(graphiti, kind, key)
    out = {"attempt_found": att is not None, "audits": [], "basis_verified": None}
    verified = bool(audits) and att is not None
    for row in audits:
        a = row["a"]
        proof = json.loads(a.get("link_proof") or "[]")
        checks = []
        for p in proof:
            nodes = await _rows(graphiti, "MATCH (e:Episodic {uuid:$u}) RETURN e.group_id AS g, "
                                          "coalesce(e.content,'') AS c", u=p["uuid"])
            ok = (len(nodes) == 1 and nodes[0]["g"] == a["group_id"] and _sha(nodes[0]["c"]) == p["content_sha256"]
                  and att is not None and ((p["basis"] == "recorded_uuid" and att["recorded"] == p["uuid"])
                                           or (p["basis"] == "content_fingerprint_after_attempt"
                                               and att["proof_key"] == p["attempt_proof_key"])))
            checks.append({"uuid": p["uuid"], "basis": p["basis"], "holds": ok})
            verified = verified and ok
        if a["outcome"] == "no_write" and att is not None:
            still_none = not await _candidates(graphiti, kind, key, att)
            checks.append({"no_provable_candidate_now": still_none})
            verified = verified and still_none
        out["audits"].append({k: (str(v) if k in ("created_at", "committed_at") else v) for k, v in a.items()
                              if k != "link_proof"} | {"checks": checks})
    out["basis_verified"] = verified if audits else None
    return out
