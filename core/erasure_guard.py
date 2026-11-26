"""Conservative read-side erasure boundary for Graphiti-backed memory.

A separate tombstone survives hard deletion. Graphiti's mutable summaries lack
complete lineage; after erasure, opaque derived content is quarantined globally.
This is containment, not verified physical purge or automatic reconstruction.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

class ErasureFenceChanged(RuntimeError):
    """The operation belongs to an obsolete erasure revision."""


# The same node/property is locked first by erase and application finalization.
# Neo4j acquires a write lock before reading a property used in its own SET.
# This serializes application-owned commit decisions, not upstream Graphiti writes.
LOCK_ERASURE_CONTROL = """
    MERGE (gate:FractalErasureControl {key:'global'})
    SET gate.fence_serial=coalesce(gate.fence_serial,0)+1
    WITH gate
"""

# Static Cypher fragments; caller values are parameters.
ERASURE_PRESENT = """(EXISTS { MATCH (:FractalErasure) }
    OR EXISTS { MATCH (d) WHERE d.deleted = true })"""

ORIGINAL_EPISODE = """(
    n:Episodic
    AND n.origin_class IN ['owner','untrusted','system']
    AND coalesce(n.recall_derived,false) = false
    AND size(coalesce(n.derived_source_ids,[])) = 0
    AND size(coalesce(n.recall_source_ids,[])) = 0
    AND size(coalesce(n.summarized_turns,[])) = 0
    AND n.provenance_id IS NULL
    AND NOT coalesce(n.episode_kind,'') IN ['chat_turn','chat_summary','l3_profile']
    AND NOT coalesce(n.name,'') IN ['chat_turn','chat_summary']
    AND NOT coalesce(n.source_description,'') STARTS WITH 'l3_profile:'
    AND coalesce(n.source_description,'') <> 'chat'
)"""


async def ensure_erasure_constraints(graphiti) -> None:
    await graphiti.driver.execute_query(
        "CREATE CONSTRAINT fractal_erasure_target_unique IF NOT EXISTS "
        "FOR (t:FractalErasure) REQUIRE t.target_uuid IS UNIQUE"
    )
    await graphiti.driver.execute_query(
        "CREATE CONSTRAINT fractal_erasure_control_unique IF NOT EXISTS "
        "FOR (c:FractalErasureControl) REQUIRE c.key IS UNIQUE"
    )


async def erase_node(graphiti, *, uuid: str, hard: bool = False) -> dict:
    """Caller enforces permission; target and tombstone change atomically."""
    if not isinstance(uuid, str) or not uuid.strip():
        raise ValueError("uuid is required")
    await ensure_erasure_constraints(graphiti)
    action = "DETACH DELETE n" if hard else "SET n.deleted=true, n.deleted_at=$ts"
    result = await graphiti.driver.execute_query(
        LOCK_ERASURE_CONTROL + """
        MATCH (n {uuid:$uuid})
        WITH gate,collect(n) AS targets
        WHERE size(targets)=1
        WITH gate,targets[0] AS n
        MERGE (t:FractalErasure {target_uuid:$uuid})
        ON CREATE SET t.created_at=$ts
        SET t.event_id=$event_id, t.group_id=n.group_id, t.mode=$mode
        SET gate.revision=coalesce(gate.revision,0)+1
        WITH n,gate
        OPTIONAL MATCH (run:TaskRun)-[]->(n)
        WITH n,gate,collect(DISTINCT run) AS runs
        FOREACH (run IN runs | SET run.erasure_quarantined=true)
        """ + action + """
        RETURN 1 AS done, gate.revision AS revision
        """,
        uuid=uuid, ts=datetime.now(timezone.utc).isoformat(),
        mode="hard" if hard else "soft", event_id=str(uuid4()),
    )
    deleted = len(result.records) == 1 and result.records[0].get("done") == 1
    return {"deleted": deleted, "mode": "hard" if hard else "soft",
            "erasure_revision": result.records[0]["revision"] if deleted else None,
            "derived_quarantine": deleted}


async def read_erasure_state(graphiti) -> tuple[int, bool]:
    """Fresh database observation; no process-local tombstone cache."""
    result = await graphiti.driver.execute_query(
        "OPTIONAL MATCH (c:FractalErasureControl {key:'global'}) "
        "RETURN coalesce(c.revision,0) AS revision, " + ERASURE_PRESENT + " AS quarantined"
    )
    if len(result.records) != 1:
        raise RuntimeError("erasure state unavailable or ambiguous")
    row = result.records[0]
    revision, quarantined = row.get("revision"), row.get("quarantined")
    if type(revision) is not int or revision < 0 or type(quarantined) is not bool:
        raise RuntimeError("invalid erasure state")
    return revision, quarantined


async def require_erasure_revision(graphiti, expected: int) -> None:
    """Fresh, fail-closed check before provider work or exposing an operation result."""
    if type(expected) is not int or expected < 0:
        raise ValueError("invalid expected erasure revision")
    revision, _ = await read_erasure_state(graphiti)
    if revision != expected:
        raise ErasureFenceChanged("erasure revision changed; start a new authorized operation")


async def require_erasure_state(graphiti, expected: tuple[int, bool]) -> None:
    """Check both revision and legacy quarantine observation, fail closed."""
    if await read_erasure_state(graphiti) != expected:
        raise ErasureFenceChanged("erasure state changed; discard stale operation")


async def quarantine_unconfirmed_episode(graphiti, episode_uuid: str) -> None:
    """Monotonic isolation of an exact known upstream write; no auto-repair."""
    await graphiti.driver.execute_query(
        "MATCH (e:Episodic {uuid:$uuid}) SET e.erasure_quarantined=true",
        uuid=episode_uuid,
    )


def candidate_key(item: dict) -> tuple[str, str, str]:
    return item["type"], item["uuid"], item["group_id"]


async def admit_search_candidates(graphiti, candidates: list[dict]) -> set[tuple[str, str, str]]:
    """Check durable identity after Graphiti search, before ranking/exposure."""
    if not candidates:
        return set()
    keys = {candidate_key(item) for item in candidates}
    rows = [{"kind": kind, "uuid": uuid, "group_id": group} for kind, uuid, group in sorted(keys)]
    result = await graphiti.driver.execute_query(
        "WITH " + ERASURE_PRESENT + """ AS quarantined
        UNWIND $candidates AS candidate
        CALL {
            WITH candidate, quarantined
            MATCH (n {uuid:candidate.uuid})
            WHERE n.group_id=candidate.group_id
              AND ((candidate.kind='episode' AND n:Episodic)
                OR (candidate.kind='entity' AND n:Entity)
                OR (candidate.kind='community' AND n:Community))
              AND coalesce(n.deleted,false)=false
              AND coalesce(n.erasure_quarantined,false)=false
              AND NOT EXISTS { MATCH (:FractalErasure {target_uuid:n.uuid}) }
              AND (NOT quarantined OR """ + ORIGINAL_EPISODE + """)
            RETURN n.uuid AS uuid
            UNION ALL
            WITH candidate, quarantined
            MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity)
            WHERE candidate.kind='edge' AND r.uuid=candidate.uuid
              AND r.group_id=candidate.group_id
              AND s.group_id=candidate.group_id AND t.group_id=candidate.group_id
              AND NOT quarantined
              AND coalesce(r.deleted,false)=false
              AND coalesce(s.deleted,false)=false AND coalesce(t.deleted,false)=false
              AND NOT EXISTS { MATCH (:FractalErasure {target_uuid:r.uuid}) }
              AND NOT EXISTS { MATCH (:FractalErasure {target_uuid:s.uuid}) }
              AND NOT EXISTS { MATCH (:FractalErasure {target_uuid:t.uuid}) }
            RETURN r.uuid AS uuid
        }
        WITH candidate, count(uuid) AS matches
        WHERE matches=1
        RETURN candidate.kind AS kind, candidate.uuid AS uuid, candidate.group_id AS group_id
        """,
        candidates=rows,
    )
    return {(row["kind"], row["uuid"], row["group_id"]) for row in result.records} & keys
