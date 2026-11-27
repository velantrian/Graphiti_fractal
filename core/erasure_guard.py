"""Variant B: scoped erasure containment for Graphiti-backed memory.

An erasure is a durable tombstone plus an *affected area* computed from lineage
(see core/erasure_scope.py). Readers restrict only objects that carry a mark of an
open erasure, plus the derived-kind objects whose lineage is unknown; groups whose
independence is shown by lineage stay fully available. Nothing here is global:
there is no whole-graph quarantine, no scan of unrelated nodes and no legacy
``deleted=true`` flag that restricts anything but the flagged object itself.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from uuid import uuid4


class ErasureFenceChanged(RuntimeError):
    """The operation belongs to an obsolete erasure revision."""


class GroupWriteRestricted(RuntimeError):
    """New model-assisted writes are paused in a group with unresolved derived objects."""


# The same node/property is locked first by erase and application finalization.
# Neo4j acquires a write lock before reading a property used in its own SET.
# This serializes application-owned commit decisions, not upstream Graphiti writes.
LOCK_ERASURE_CONTROL = """
    MERGE (gate:FractalErasureControl {key:'global'})
    SET gate.fence_serial=coalesce(gate.fence_serial,0)+1
    WITH gate
"""

OPEN_STATUSES = "['PENDING_SCOPE','SCOPED']"
OPEN_TOMBSTONE = "(t.status IS NULL OR t.status IN " + OPEN_STATUSES + ")"   # callers bind `t` themselves

_TOKEN = re.compile(r"[A-Za-z0-9Ѐ-ӿ][A-Za-z0-9Ѐ-ӿ_-]{4,}")


def token_hashes(text: str, cap: int = 600) -> list[str]:
    """Hashed distinctive-candidate tokens of a text (the erased text itself is not retained)."""
    found: set[str] = set()
    for token in _TOKEN.findall((text or "").lower()):
        found.add(token)
        found.update(part for part in re.split(r"[-_]", token) if len(part) >= 5)
    return sorted(hashlib.sha256(t.encode()).hexdigest()[:16] for t in found)[:cap]


# --- reader predicates (object level; no scans of unrelated nodes) -----------------------
def _sub(template: str, alias: str) -> str:
    return template.replace("@N", alias)


_DERIVED_KIND = """(@N:Episodic AND (
    coalesce(@N.episode_kind,'') IN ['chat_turn','chat_summary'] OR coalesce(@N.name,'') IN ['chat_turn','chat_summary']
    OR coalesce(@N.source_description,'') = 'chat'
    OR coalesce(@N.source_description,'') STARTS WITH 'l3_profile:'))"""

_UNKNOWN_LINEAGE = """(""" + _DERIVED_KIND + """
    AND NOT coalesce(@N.chat_finalized,false) AND NOT coalesce(@N.lineage_reviewed,false)
    AND size(coalesce(@N.derived_source_ids,[])) = 0 AND size(coalesce(@N.recall_source_ids,[])) = 0
    AND size(coalesce(@N.summarized_turns,[])) = 0)"""

_ORIGINAL = """(@N:Episodic AND coalesce(@N.origin_class,'') IN ['owner','untrusted','system']
    AND coalesce(@N.recall_derived,false) = false
    AND size(coalesce(@N.derived_source_ids,[])) = 0 AND size(coalesce(@N.recall_source_ids,[])) = 0
    AND size(coalesce(@N.summarized_turns,[])) = 0 AND @N.provenance_id IS NULL
    AND NOT (""" + _DERIVED_KIND + """))"""

# A tombstone whose area is not (yet) computed: being scoped, or created without a lineage seed.
# Fail closed for its own group's derived objects only.
# (variable names inside EXISTS must not collide with the caller's, e.g. edge endpoints `t`)
_UNSCOPED = "(fz_t.status IS NULL OR fz_t.status = 'PENDING_SCOPE')"
_PENDING_GROUP = "EXISTS { MATCH (fz_t:FractalErasure {group_id:@N.group_id}) WHERE " + _UNSCOPED + " }"
_PENDING_ANY = "EXISTS { MATCH (fz_t:FractalErasure) WHERE " + _UNSCOPED + " }"
_ANY_TOMBSTONE = "EXISTS { MATCH (:FractalErasure) }"


def blocked_node(alias: str) -> str:
    """True when readers must not return this node.

    Object level: the object is deleted, tombstoned, individually quarantined, or carries a
    mark of an open erasure. While an erasure is still being scoped (a short, crash-safe
    window) derived objects of its group, and derived-kind episodes anywhere, are held.
    Derived-kind episodes (chat/L3) with unknown lineage are held once any tombstone exists.
    """
    return _sub("""(
        coalesce(@N.deleted,false) = true
        OR coalesce(@N.erasure_quarantined,false) = true
        OR size(coalesce(@N.erasure_quarantine,[])) > 0
        OR EXISTS { MATCH (:FractalErasure {target_uuid:@N.uuid}) }
        OR (NOT @N:Episodic AND """ + _PENDING_GROUP + """)
        OR (@N:Episodic AND NOT """ + _ORIGINAL + """ AND """ + _PENDING_GROUP + """)
        OR (NOT @N:Episodic AND """ + _UNSETTLED_GROUP + """)
        OR (@N:Episodic AND NOT """ + _ORIGINAL + """ AND """ + _UNSETTLED_GROUP + """)
        OR (""" + _DERIVED_KIND + """ AND """ + _PENDING_ANY + """)
        OR (""" + _UNKNOWN_LINEAGE + """ AND """ + _ANY_TOMBSTONE + """)
    )""", alias)


def unknown_lineage(alias: str) -> str:
    """Derived-kind episode (chat/summary/L3) whose lineage was never recorded."""
    return _sub(_UNKNOWN_LINEAGE, alias)


def visible_node(alias: str) -> str:
    return "NOT " + blocked_node(alias)


def visible_edge(rel: str, start: str, end: str) -> str:
    own = _sub("""(coalesce(@N.deleted,false) = false
        AND size(coalesce(@N.erasure_quarantine,[])) = 0
        AND NOT EXISTS { MATCH (:FractalErasure {target_uuid:@N.uuid}) }
        AND NOT """ + _PENDING_GROUP + ")", rel)
    return own + " AND " + visible_node(start) + " AND " + visible_node(end)


# --- state ----------------------------------------------------------------------------
async def _create_schema(graphiti, statement: str) -> None:
    """``IF NOT EXISTS`` is not race-free: two connections creating the same schema rule at the
    same moment make the loser fail with ...SchemaRuleAlreadyExists. That outcome is the one we
    wanted, so it is not an error; anything else is raised."""
    try:
        await graphiti.driver.execute_query(statement)
    except Exception as exc:  # noqa: BLE001 - narrowed by the server code below
        code = str(getattr(exc, "code", "") or "") + " " + str(exc)
        if "AlreadyExists" not in code:
            raise


async def ensure_erasure_constraints(graphiti) -> None:
    await _create_schema(graphiti,
        "CREATE CONSTRAINT fractal_erasure_target_unique IF NOT EXISTS "
        "FOR (t:FractalErasure) REQUIRE t.target_uuid IS UNIQUE")
    await _create_schema(graphiti,
        "CREATE CONSTRAINT fractal_erasure_control_unique IF NOT EXISTS "
        "FOR (c:FractalErasureControl) REQUIRE c.key IS UNIQUE")
    await _create_schema(graphiti,
        "CREATE INDEX fractal_erasure_status IF NOT EXISTS FOR (t:FractalErasure) ON (t.status)")


async def read_erasure_state(graphiti) -> tuple[int, bool]:
    """(revision, any open erasure). A fresh observation of two tiny labels; no node scan."""
    result = await graphiti.driver.execute_query(
        "OPTIONAL MATCH (c:FractalErasureControl {key:'global'}) "
        "WITH coalesce(c.revision,0) AS revision "
        "RETURN revision, EXISTS { MATCH (t:FractalErasure) WHERE " + OPEN_TOMBSTONE + " } AS open"
    )
    if len(result.records) != 1:
        raise RuntimeError("erasure state unavailable or ambiguous")
    row = result.records[0]
    revision, is_open = row.get("revision"), row.get("open")
    if type(revision) is not int or revision < 0 or type(is_open) is not bool:
        raise RuntimeError("invalid erasure state")
    return revision, is_open


async def last_erasure_at(graphiti):
    """Time of the most recent erasure (None if there never was one)."""
    result = await graphiti.driver.execute_query("OPTIONAL MATCH (t:FractalErasure) RETURN max(t.erased_at) AS at")
    value = result.records[0]["at"] if result.records else None
    return datetime.fromisoformat(value) if value else None


async def require_erasure_revision(graphiti, expected: int, group_id: str | None = None) -> None:
    """Fresh, fail-closed check before provider work or exposing an operation result.

    With ``group_id`` only erasures in that group invalidate the operation (document ingest
    reads only that group); without it any erasure does (chat reads every group).
    """
    if type(expected) is not int or expected < 0:
        raise ValueError("invalid expected erasure revision")
    if group_id is None:
        revision, _ = await read_erasure_state(graphiti)
        if revision != expected:
            raise ErasureFenceChanged("erasure revision changed; start a new authorized operation")
        return
    result = await graphiti.driver.execute_query(
        "OPTIONAL MATCH (t:FractalErasure {group_id:$g}) WHERE t.revision > $r RETURN count(t) AS n",
        g=group_id, r=expected)
    if int(result.records[0]["n"]) > 0:
        raise ErasureFenceChanged("an erasure in this group happened meanwhile; start a new authorized operation")


async def require_erasure_state(graphiti, expected: tuple[int, bool]) -> None:
    """Fence on the erasure revision. The second element is informational (open erasures
    can resolve without any deletion) and does not invalidate an operation."""
    revision, _ = await read_erasure_state(graphiti)
    if revision != expected[0]:
        raise ErasureFenceChanged("erasure revision changed; discard stale operation")


# A write that reached Graphiti (durable write-attempt marker) and whose outcome was never settled,
# while an erasure newer than the write's snapshot exists: which objects it created is unknown, so
# nothing can be marked and the group cannot be declared safe. Derived from durable records, so it
# holds after a process restart without any in-memory state.
_UNSETTLED_INGEST_T = """{c}.state IN ['PENDING','RECONCILIATION_REQUIRED'] AND {c}.write_attempted_at IS NOT NULL
        AND {c}.late_settled_at IS NULL"""
UNSETTLED_INGEST = _UNSETTLED_INGEST_T.format(c="c")
UNSETTLED_CHAT = "a.state = 'OPEN'"
# Read side of the same fact (variable names unique: callers bind s/t/r/n/m/e/c/a). While such an attempt
# exists in a node's group, the group's DERIVED objects (entities, edges, communities, non-original episodes)
# are held from every reader: the unknown write may have stored text of an erased object in any of them,
# and nothing can be marked because it is unknown which.
_UNSETTLED_GROUP = ("EXISTS { MATCH (fz_c:FractalIngestClaim|FractalChatWriteAttempt {group_id:@N.group_id}) "
                    "WHERE fz_c.state IN ['OPEN','PENDING','RECONCILIATION_REQUIRED'] AND fz_c.late_settled_at IS NULL "
                    "AND (fz_c:FractalChatWriteAttempt OR fz_c.write_attempted_at IS NOT NULL) "
                    "AND EXISTS { MATCH (fz_u:FractalErasure) WHERE fz_u.revision > coalesce(fz_c.erasure_revision,0) "
                    "AND (fz_c:FractalChatWriteAttempt OR fz_u.group_id = fz_c.group_id) } }")


async def require_group_writable(graphiti, group_id: str) -> None:
    """Pause model-assisted writes only in groups with unresolved Graphiti-derived objects.

    Those objects would be fed back to the model as candidates by add_episode; hiding
    them at read time does not help. Other groups are unaffected.
    """
    result = await graphiti.driver.execute_query(
        "OPTIONAL MATCH (t:FractalErasure) WHERE " + OPEN_TOMBSTONE
        + " AND $g IN coalesce(t.blocked_groups, [t.group_id]) WITH count(t) AS n RETURN n, "
        + "EXISTS { MATCH (c:FractalIngestClaim {group_id:$g}) WHERE " + UNSETTLED_INGEST
        + " AND EXISTS { MATCH (u:FractalErasure {group_id:$g}) WHERE u.revision > coalesce(c.erasure_revision,0) } }"
        + " AS ingest_unsettled, "
        + "EXISTS { MATCH (a:FractalChatWriteAttempt {group_id:$g}) WHERE " + UNSETTLED_CHAT
        + " AND EXISTS { MATCH (u:FractalErasure) WHERE u.revision > coalesce(a.erasure_revision,0) } }"
        + " AS chat_unsettled",
        g=group_id,
    )
    if len(result.records) != 1:
        raise RuntimeError("write restriction state unavailable")
    row = result.records[0]
    if row["ingest_unsettled"] or row["chat_unsettled"]:
        raise GroupWriteRestricted(
            f"group {group_id!r}: a write whose outcome is unknown crossed an erasure; it must be settled "
            "(core.settlement.settle_unsettled_write) before model-assisted writes resume")
    if int(row["n"]) > 0:
        raise GroupWriteRestricted(
            f"group {group_id!r} has derived objects of an erasure that are not yet rebuilt or cleared")


async def live_previous_episode_uuids(graphiti, group_id: str, reference_time, limit: int = 10) -> list[str] | None:
    """The previous-episode window for add_episode without erased/quarantined episodes.

    Graphiti's default window ignores Fractal's deletion state. Returns None (use Graphiti's
    default) when the group has no tombstone, so unaffected groups behave exactly as before.
    """
    has = await graphiti.driver.execute_query(
        "OPTIONAL MATCH (t:FractalErasure {group_id:$g}) RETURN count(t) AS n", g=group_id)
    if int(has.records[0]["n"]) == 0:
        return None
    rows = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic {group_id:$g})
        WHERE e.valid_at <= $ref AND """ + visible_node("e") + """
        RETURN e.uuid AS uuid ORDER BY e.valid_at DESC LIMIT $limit
        """,
        g=group_id, ref=reference_time, limit=limit,
    )
    return [r["uuid"] for r in reversed(rows.records)]


async def _content_hashes(graphiti, uuid: str) -> list[str]:
    result = await graphiti.driver.execute_query(
        "MATCH (n:Episodic {uuid:$u}) RETURN coalesce(n.content, n.episode_body, '') AS c", u=uuid)
    return token_hashes(result.records[0]["c"]) if result.records else []


_NEW_EVENT = LOCK_ERASURE_CONTROL + """
        MATCH (n {uuid:$uuid})
        WITH gate,collect(n) AS targets
        WHERE size(targets)=1
        WITH gate,targets[0] AS n
        WHERE NOT (coalesce(n.deleted,false)=true
                   AND EXISTS { MATCH (:FractalErasure {target_uuid:$uuid}) })
        OPTIONAL MATCH (n)-[:MENTIONS]->(m:Entity)
        WITH gate,n,collect(DISTINCT m.uuid) AS mentioned
        OPTIONAL MATCH ()-[r:RELATES_TO]->()
          WHERE r.group_id = n.group_id
            AND (n.uuid IN coalesce(r.episodes,[]) OR r.uuid IN coalesce(n.entity_edges,[]))
        WITH gate,n,mentioned,collect(DISTINCT r.uuid) AS edges
        MERGE (t:FractalErasure {target_uuid:$uuid})
        ON CREATE SET t.event_id=$event_id, t.created_at=$ts, t.group_id=n.group_id, t.mode=$mode,
            t.status='PENDING_SCOPE', t.target_label=head(labels(n)), t.valid_at=toString(n.valid_at),
            t.seed_entities=mentioned, t.seed_edges=edges, t.token_hashes=$hashes,
            t.blocked_groups=[n.group_id], t.erased_at=$ts, t.revision=coalesce(gate.revision,0)+1,
            gate.revision=coalesce(gate.revision,0)+1
        WITH n,gate,t,(t.event_id=$event_id) AS fresh
        OPTIONAL MATCH (run:TaskRun)-[]->(n)
        WITH n,gate,t,fresh,collect(DISTINCT run) AS runs
        FOREACH (run IN runs | SET run.erasure_quarantined=true)
        FOREACH (_ IN CASE WHEN $hard THEN [1] ELSE [] END | SET t.hard_deleted_at=coalesce(t.hard_deleted_at,$ts))
        FOREACH (_ IN CASE WHEN $hard THEN [] ELSE [1] END | SET n.deleted=true, n.deleted_at=coalesce(n.deleted_at,$ts))
        WITH n,gate,t,fresh,$hard AS hard
        WITH n,gate,t,fresh,hard,t.event_id AS event_id,t.revision AS event_revision,
             gate.revision AS database_revision,t.mode AS event_mode,t.status AS event_status
        FOREACH (_ IN CASE WHEN hard THEN [1] ELSE [] END | DETACH DELETE n)
        RETURN fresh,event_id,event_revision,database_revision,event_mode,event_status
        """

# Already erased (soft flag or physically gone): read the event, upgrade soft->hard when asked.
_KNOWN_EVENT = LOCK_ERASURE_CONTROL + """
        OPTIONAL MATCH (n {uuid:$uuid})
        WITH gate,collect(n) AS targets
        OPTIONAL MATCH (t:FractalErasure {target_uuid:$uuid})
        WITH gate,targets,t,($hard AND t IS NOT NULL AND size(targets)=1) AS upgrade
        FOREACH (_ IN CASE WHEN upgrade THEN [1] ELSE [] END |
                 SET t.hard_deleted_at=coalesce(t.hard_deleted_at,$ts))
        FOREACH (x IN CASE WHEN upgrade THEN targets ELSE [] END | DETACH DELETE x)
        RETURN t IS NOT NULL AS known, upgrade, size(targets) AS present, t.event_id AS event_id,
               t.revision AS event_revision, gate.revision AS database_revision,
               t.mode AS event_mode, t.status AS event_status
        """


async def erase_node(graphiti, *, uuid: str, hard: bool = False) -> dict:
    """Caller enforces permission. Idempotent per target UUID.

    Phase 1 is one lock-serialized statement: the first request creates the event (tombstone with
    the lineage seed, event_id and event revision fixed ON CREATE, one database-revision bump) and
    performs the requested mode. Every later request - concurrent, after the area was rebuilt, after
    the node was physically deleted - finds that event and never rewrites its identity, seed or
    status. A hard request after a soft one deletes the node physically and records
    ``hard_deleted_at``; it does not reopen the event or bump any revision.
    Phase 2 (core.erasure_scope.scope_erasure) is run only by the creating request; if the process
    dies in between, the group stays held (PENDING_SCOPE) and scope_pending_erasures() finishes it.

    The result separates ``event_revision`` (database revision at which this event was created,
    fixed) from ``database_revision`` (current, global) and states ``performed_mode`` - what this
    call actually did: "soft", "hard" or "none" (``requested_mode`` is what was asked)."""
    if not isinstance(uuid, str) or not uuid.strip():
        raise ValueError("uuid is required")
    await ensure_erasure_constraints(graphiti)
    hashes = await _content_hashes(graphiti, uuid)
    mode = "hard" if hard else "soft"
    ts = datetime.now(timezone.utc).isoformat()
    out = {"found": False, "deleted": False, "requested_mode": mode, "performed_mode": "none",
           "mode": "none", "idempotent_repeat": False, "event_id": None, "event_revision": None,
           "erasure_revision": None, "database_revision": None, "event_mode": None,
           "event_status": None, "node_state": "absent", "area": None}
    event_id = str(uuid4())
    result = await graphiti.driver.execute_query(
        _NEW_EVENT, uuid=uuid, ts=ts, mode=mode, event_id=event_id, hashes=hashes, hard=hard)
    if len(result.records) == 1:
        row = result.records[0]
        out.update(found=True, deleted=True, performed_mode=mode, mode=mode,
                   idempotent_repeat=not row["fresh"], event_id=row["event_id"],
                   event_revision=row["event_revision"], erasure_revision=row["event_revision"],
                   database_revision=row["database_revision"], event_mode=row["event_mode"],
                   event_status=row["event_status"], node_state="absent" if hard else "soft")
        if row["fresh"]:
            from core.erasure_scope import scope_erasure
            out["area"] = await scope_erasure(graphiti, row["event_id"])
            out["event_status"] = out["area"].get("status")
        return out
    result = await graphiti.driver.execute_query(_KNOWN_EVENT, uuid=uuid, ts=ts, hard=hard)
    if len(result.records) != 1 or not result.records[0]["known"]:
        return out
    row = result.records[0]
    performed = "hard" if row["upgrade"] else "none"
    out.update(found=True, deleted=True, performed_mode=performed, mode=performed, idempotent_repeat=True,
               event_id=row["event_id"], event_revision=row["event_revision"],
               erasure_revision=row["event_revision"], database_revision=row["database_revision"],
               event_mode=row["event_mode"], event_status=row["event_status"],
               node_state="absent" if (row["upgrade"] or not row["present"]) else "soft")
    return out


async def quarantine_unconfirmed_episode(graphiti, episode_uuid: str) -> None:
    """Monotonic isolation of an exact known upstream write; no auto-repair."""
    await graphiti.driver.execute_query(
        "MATCH (e:Episodic {uuid:$uuid}) SET e.erasure_quarantined=true",
        uuid=episode_uuid,
    )


def candidate_key(item: dict) -> tuple[str, str, str]:
    return item["type"], item["uuid"], item["group_id"]


RAW_TEXT = "_raw_text"   # key under which search code carries the text exactly as Graphiti returned it


async def admit_search_candidates(graphiti, candidates: list[dict]) -> set[tuple[str, str, str]]:
    """Check durable identity, erasure state and freshness of text, after Graphiti search, before ranking.

    One batched statement. A candidate that carries ``_raw_text`` = [t1, t2] (the text Graphiti returned,
    BEFORE any truncation for display) is admitted only if the same fields in Neo4j are still exactly equal:
    episode content; entity name+summary; edge fact+name; community name+summary. If the text changed after the
    search, the candidate is excluded: its relevance score was computed for the old text and must not pass to a
    new one. Candidates without ``_raw_text`` are checked for identity and erasure state only.
    """
    if not candidates:
        return set()
    by_key: dict[tuple[str, str, str], list | None] = {}
    for item in candidates:
        raw = item.get(RAW_TEXT)
        by_key[candidate_key(item)] = list(raw) if raw is not None else None
    keys = set(by_key)
    rows = [{"kind": kind, "uuid": uuid, "group_id": group,
             "check": by_key[(kind, uuid, group)] is not None,
             "t1": (by_key[(kind, uuid, group)] or ["", ""])[0],
             "t2": (by_key[(kind, uuid, group)] or ["", ""])[1]} for kind, uuid, group in sorted(keys)]
    result = await graphiti.driver.execute_query(
        """
        UNWIND $candidates AS candidate
        CALL (candidate) {
            MATCH (n:Episodic {uuid:candidate.uuid})
            WHERE candidate.kind='episode' AND n.group_id=candidate.group_id AND """ + visible_node("n") + """
              AND (NOT candidate.check OR coalesce(n.content, n.summary, '') = candidate.t1)
            RETURN n.uuid AS uuid
            UNION ALL
            MATCH (n:Entity {uuid:candidate.uuid})
            WHERE candidate.kind='entity' AND n.group_id=candidate.group_id AND """ + visible_node("n") + """
              AND (NOT candidate.check OR (coalesce(n.name,'') = candidate.t1 AND coalesce(n.summary,'') = candidate.t2))
            RETURN n.uuid AS uuid
            UNION ALL
            MATCH (n:Community {uuid:candidate.uuid})
            WHERE candidate.kind='community' AND n.group_id=candidate.group_id AND """ + visible_node("n") + """
              AND (NOT candidate.check OR (coalesce(n.name,'') = candidate.t1 AND coalesce(n.summary,'') = candidate.t2))
            RETURN n.uuid AS uuid
            UNION ALL
            MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity)
            WHERE candidate.kind='edge' AND r.uuid=candidate.uuid
              AND r.group_id=candidate.group_id
              AND s.group_id=candidate.group_id AND t.group_id=candidate.group_id
              AND """ + visible_edge("r", "s", "t") + """
              AND (NOT candidate.check OR (coalesce(r.fact,'') = candidate.t1 AND coalesce(r.name,'') = candidate.t2))
            RETURN r.uuid AS uuid
        }
        WITH candidate, count(uuid) AS matches
        WHERE matches=1
        RETURN candidate.kind AS kind, candidate.uuid AS uuid, candidate.group_id AS group_id
        """,
        candidates=rows,
    )
    return {(row["kind"], row["uuid"], row["group_id"]) for row in result.records} & keys
