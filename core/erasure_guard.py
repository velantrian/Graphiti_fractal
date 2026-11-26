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
        OR (""" + _DERIVED_KIND + """ AND """ + _PENDING_ANY + """)
        OR (""" + _UNKNOWN_LINEAGE + """ AND """ + _ANY_TOMBSTONE + """)
    )""", alias)


def visible_node(alias: str) -> str:
    return "NOT " + blocked_node(alias)


def visible_edge(rel: str, start: str, end: str) -> str:
    own = _sub("""(coalesce(@N.deleted,false) = false
        AND size(coalesce(@N.erasure_quarantine,[])) = 0
        AND NOT EXISTS { MATCH (:FractalErasure {target_uuid:@N.uuid}) }
        AND NOT """ + _PENDING_GROUP + ")", rel)
    return own + " AND " + visible_node(start) + " AND " + visible_node(end)


# --- state ----------------------------------------------------------------------------
async def ensure_erasure_constraints(graphiti) -> None:
    await graphiti.driver.execute_query(
        "CREATE CONSTRAINT fractal_erasure_target_unique IF NOT EXISTS "
        "FOR (t:FractalErasure) REQUIRE t.target_uuid IS UNIQUE"
    )
    await graphiti.driver.execute_query(
        "CREATE CONSTRAINT fractal_erasure_control_unique IF NOT EXISTS "
        "FOR (c:FractalErasureControl) REQUIRE c.key IS UNIQUE"
    )
    await graphiti.driver.execute_query(
        "CREATE INDEX fractal_erasure_status IF NOT EXISTS FOR (t:FractalErasure) ON (t.status)"
    )


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


async def require_group_writable(graphiti, group_id: str) -> None:
    """Pause model-assisted writes only in groups with unresolved Graphiti-derived objects.

    Those objects would be fed back to the model as candidates by add_episode; hiding
    them at read time does not help. Other groups are unaffected.
    """
    result = await graphiti.driver.execute_query(
        "OPTIONAL MATCH (t:FractalErasure) WHERE " + OPEN_TOMBSTONE
        + " AND $g IN coalesce(t.blocked_groups, [t.group_id]) RETURN count(t) AS n",
        g=group_id,
    )
    if len(result.records) != 1:
        raise RuntimeError("write restriction state unavailable")
    if int(result.records[0]["n"]) > 0:
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


async def erase_node(graphiti, *, uuid: str, hard: bool = False) -> dict:
    """Caller enforces permission. Phase 1 is one atomic statement: tombstone with the lineage
    seed (mentioned entities, edges, group, hashed tokens), revision bump, soft/hard delete.
    Phase 2 (core.erasure_scope.scope_erasure) computes and marks the affected area and runs
    immediately; if the process dies in between, the group stays held (PENDING_SCOPE) and
    scope_pending_erasures() finishes the job."""
    if not isinstance(uuid, str) or not uuid.strip():
        raise ValueError("uuid is required")
    await ensure_erasure_constraints(graphiti)
    hashes = await _content_hashes(graphiti, uuid)
    action = "DETACH DELETE n" if hard else "SET n.deleted=true, n.deleted_at=$ts"
    event_id = str(uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    result = await graphiti.driver.execute_query(
        LOCK_ERASURE_CONTROL + """
        MATCH (n {uuid:$uuid})
        WITH gate,collect(n) AS targets
        WHERE size(targets)=1
        WITH gate,targets[0] AS n
        OPTIONAL MATCH (n)-[:MENTIONS]->(m:Entity)
        WITH gate,n,collect(DISTINCT m.uuid) AS mentioned
        OPTIONAL MATCH ()-[r:RELATES_TO]->()
          WHERE r.group_id = n.group_id
            AND (n.uuid IN coalesce(r.episodes,[]) OR r.uuid IN coalesce(n.entity_edges,[]))
        WITH gate,n,mentioned,collect(DISTINCT r.uuid) AS edges
        MERGE (t:FractalErasure {target_uuid:$uuid})
        ON CREATE SET t.created_at=$ts
        SET t.event_id=$event_id, t.group_id=n.group_id, t.mode=$mode, t.status='PENDING_SCOPE',
            t.target_label=head(labels(n)), t.valid_at=toString(n.valid_at),
            t.seed_entities=mentioned, t.seed_edges=edges, t.token_hashes=$hashes,
            t.blocked_groups=[n.group_id], t.erased_at=$ts, t.revision=coalesce(gate.revision,0)+1
        SET gate.revision=coalesce(gate.revision,0)+1
        WITH n,gate
        OPTIONAL MATCH (run:TaskRun)-[]->(n)
        WITH n,gate,collect(DISTINCT run) AS runs
        FOREACH (run IN runs | SET run.erasure_quarantined=true)
        """ + action + """
        RETURN 1 AS done, gate.revision AS revision
        """,
        uuid=uuid, ts=ts, mode="hard" if hard else "soft", event_id=event_id, hashes=hashes,
    )
    deleted = len(result.records) == 1 and result.records[0].get("done") == 1
    out = {"deleted": deleted, "mode": "hard" if hard else "soft",
           "erasure_revision": result.records[0]["revision"] if deleted else None,
           "event_id": event_id if deleted else None, "area": None}
    if deleted:
        from core.erasure_scope import scope_erasure
        out["area"] = await scope_erasure(graphiti, event_id)
    return out


async def quarantine_unconfirmed_episode(graphiti, episode_uuid: str) -> None:
    """Monotonic isolation of an exact known upstream write; no auto-repair."""
    await graphiti.driver.execute_query(
        "MATCH (e:Episodic {uuid:$uuid}) SET e.erasure_quarantined=true",
        uuid=episode_uuid,
    )


def candidate_key(item: dict) -> tuple[str, str, str]:
    return item["type"], item["uuid"], item["group_id"]


async def admit_search_candidates(graphiti, candidates: list[dict]) -> set[tuple[str, str, str]]:
    """Check durable identity and erasure state after Graphiti search, before ranking/exposure."""
    if not candidates:
        return set()
    keys = {candidate_key(item) for item in candidates}
    rows = [{"kind": kind, "uuid": uuid, "group_id": group} for kind, uuid, group in sorted(keys)]
    result = await graphiti.driver.execute_query(
        """
        UNWIND $candidates AS candidate
        CALL (candidate) {
            MATCH (n:Episodic {uuid:candidate.uuid})
            WHERE candidate.kind='episode' AND n.group_id=candidate.group_id AND """ + visible_node("n") + """
            RETURN n.uuid AS uuid
            UNION ALL
            MATCH (n:Entity {uuid:candidate.uuid})
            WHERE candidate.kind='entity' AND n.group_id=candidate.group_id AND """ + visible_node("n") + """
            RETURN n.uuid AS uuid
            UNION ALL
            MATCH (n:Community {uuid:candidate.uuid})
            WHERE candidate.kind='community' AND n.group_id=candidate.group_id AND """ + visible_node("n") + """
            RETURN n.uuid AS uuid
            UNION ALL
            MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity)
            WHERE candidate.kind='edge' AND r.uuid=candidate.uuid
              AND r.group_id=candidate.group_id
              AND s.group_id=candidate.group_id AND t.group_id=candidate.group_id
              AND """ + visible_edge("r", "s", "t") + """
            RETURN r.uuid AS uuid
        }
        WITH candidate, count(uuid) AS matches
        WHERE matches=1
        RETURN candidate.kind AS kind, candidate.uuid AS uuid, candidate.group_id AS group_id
        """,
        candidates=rows,
    )
    return {(row["kind"], row["uuid"], row["group_id"]) for row in result.records} & keys
