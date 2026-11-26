"""App-level ingest claims and exact-UUID finalization.

This layer does not pretend to make Graphiti's internal write transaction part of
our transaction. It closes Fractal-side concurrent duplicate races and makes
post-Graphiti fingerprint/group/authorship/origin finalization atomic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from neo4j.exceptions import ConstraintError

from core.erasure_guard import (
    ErasureFenceChanged, LOCK_ERASURE_CONTROL, ensure_erasure_constraints,
)


VALID_ORIGIN_CLASSES = {"owner", "agent_derived", "untrusted", "system"}


async def ensure_ingest_claim_constraint(graphiti) -> None:
    await graphiti.driver.execute_query(
        """
        CREATE CONSTRAINT fractal_ingest_claim_unique IF NOT EXISTS
        FOR (c:FractalIngestClaim)
        REQUIRE c.claim_key IS UNIQUE
        """
    )


def claim_key(group_id: str, fingerprint: str) -> str:
    if not group_id or not fingerprint:
        raise ValueError("group_id and fingerprint are required")
    return f"{group_id}:{fingerprint}"


async def acquire_ingest_claim(
    graphiti,
    *,
    group_id: str,
    fingerprint: str,
    ttl_seconds: int = 300,
    expected_revision: int | None = None,
) -> str | None:
    """Acquire one unique claim or return None when another claim owns identity."""
    await ensure_ingest_claim_constraint(graphiti)
    await ensure_erasure_constraints(graphiti)
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
        raise ValueError("invalid expected erasure revision")
    key = claim_key(group_id, fingerprint)
    token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=max(30, ttl_seconds))

    # A timestamp is diagnostic, not proof that Graphiti did not write.
    # Never reclaim a claim automatically: the upstream transaction is not fenced
    # by our token, and a slow or crashed writer may already have committed.
    try:
        result = await graphiti.driver.execute_query(
            LOCK_ERASURE_CONTROL + """
            WHERE $expected_revision IS NULL OR coalesce(gate.revision,0)=$expected_revision
            CREATE (c:FractalIngestClaim {
                claim_key:$claim_key,
                group_id:$group_id,
                fingerprint:$fingerprint,
                token:$token,
                state:'PENDING',
                erasure_revision:coalesce(gate.revision,0),
                created_at:datetime($now),
                expires_at:datetime($expires)
            })
            RETURN c.token AS token
            """,
            claim_key=key,
            group_id=group_id,
            fingerprint=fingerprint,
            token=token,
            now=now.isoformat(),
            expires=expires.isoformat(),
            expected_revision=expected_revision,
        )
        if not result.records:
            raise ErasureFenceChanged("ingest claim not admitted at expected revision")
        return str(result.records[0]["token"])
    except ConstraintError:
        # Only a uniqueness conflict is evidence of an existing claim.
        # Database outages, auth errors, and query failures must propagate.
        return None


async def release_ingest_claim(
    graphiti,
    *,
    group_id: str,
    fingerprint: str,
    token: str,
    confirmed_no_write: bool = False,
) -> None:
    """Explicit recovery only, after independent proof no upstream write can land."""
    if confirmed_no_write is not True:
        raise PermissionError("claim release requires confirmed no-write evidence")
    await graphiti.driver.execute_query(
        """
        MATCH (c:FractalIngestClaim {claim_key:$claim_key, token:$token, state:'PENDING'})
        WHERE c.episode_uuid IS NULL
        DELETE c
        """,
        claim_key=claim_key(group_id, fingerprint),
        token=token,
    )


async def mark_ingest_claim_episode_created(
    graphiti,
    *,
    group_id: str,
    fingerprint: str,
    token: str,
    episode_uuid: str,
) -> None:
    result = await graphiti.driver.execute_query(
        """
        MATCH (c:FractalIngestClaim {claim_key:$claim_key, token:$token, state:'PENDING'})
        SET c.episode_uuid=$episode_uuid, c.episode_created_at=datetime()
        RETURN c.token AS token
        """,
        claim_key=claim_key(group_id, fingerprint),
        token=token,
        episode_uuid=episode_uuid,
    )
    if not result.records:
        raise RuntimeError("ingest claim disappeared after Graphiti episode creation")


async def mark_ingest_claim_failed(
    graphiti,
    *,
    group_id: str,
    fingerprint: str,
    token: str,
    error_type: str,
) -> None:
    """Keep ambiguous claims durable; never overwrite an already committed claim."""
    await graphiti.driver.execute_query(
        """
        MATCH (c:FractalIngestClaim {claim_key:$claim_key, token:$token, state:'PENDING'})
        SET c.state='RECONCILIATION_REQUIRED',
            c.failed_at=datetime(), c.error_type=$error_type
        WITH c
        OPTIONAL MATCH (e:Episodic {uuid:c.episode_uuid})
        FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END |
            SET e.erasure_quarantined=true)
        """,
        claim_key=claim_key(group_id, fingerprint),
        token=token,
        error_type=error_type[:120],
    )


async def read_ingest_claim(graphiti, *, group_id: str, fingerprint: str) -> dict:
    """Read a durable per-chunk receipt, including after a process restart.

    No replay, release, promotion or repair is authorized by this receipt.
    Absence of a claim is not proof that no upstream write happened.
    """
    result = await graphiti.driver.execute_query(
        """
        MATCH (c:FractalIngestClaim {claim_key:$claim_key})
        OPTIONAL MATCH (e:Episodic {uuid:c.episode_uuid})
        RETURN c.state AS state, c.episode_uuid AS episode_uuid,
               c.ingest_quality AS ingest_quality, c.content_sha256 AS content_sha256,
               c.error_type AS error_type,
               (e IS NOT NULL AND coalesce(e.deleted,false)=false
                AND coalesce(e.erasure_quarantined,false)=false
                AND NOT EXISTS { MATCH (:FractalErasure {target_uuid:e.uuid}) }) AS episode_live
        """,
        claim_key=claim_key(group_id, fingerprint),
    )
    if len(result.records) != 1:
        return {"status": "unknown", "writes_performed": False}
    row = dict(result.records[0])
    if row.get("state") == "COMMITTED" and row.get("episode_live") is True:
        status = {"complete": "committed_complete", "degraded": "committed_degraded"}.get(
            row.get("ingest_quality"), "unconfirmed")
    else:
        status = "unconfirmed"
    return {**row, "status": status, "writes_performed": False}



async def classify_episode_origin(
    graphiti,
    *,
    episode_uuid: str,
    origin_class: str,
    authoritative_fact: bool = False,
) -> None:
    """Classify an exact existing episode and propagate non-owner Entity taint.

    This is for direct Graphiti write paths that cannot use the Fractal ingest
    claim workflow (for example persisted chat turns/summaries). Unknown origin
    is never inferred. Non-owner taint is monotonic and therefore fail-closed.
    """
    if origin_class not in VALID_ORIGIN_CLASSES:
        raise ValueError(f"invalid origin_class: {origin_class!r}")

    result = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic {uuid:$episode_uuid})
        SET e.origin_class=$origin_class,
            e.authoritative_fact=$authoritative_fact
        WITH e
        OPTIONAL MATCH (e)-[:MENTIONS]->(n:Entity)
        FOREACH (_ IN CASE
            WHEN n IS NULL OR $origin_class = 'owner' THEN []
            ELSE [1]
        END | SET n.has_non_owner_source=true)
        RETURN e.uuid AS uuid
        """,
        episode_uuid=episode_uuid,
        origin_class=origin_class,
        authoritative_fact=authoritative_fact,
    )
    if not result.records:
        raise LookupError(f"episode not found for origin classification: {episode_uuid}")


async def finalize_episode_identity(
    graphiti,
    *,
    episode_uuid: str,
    group_id: str,
    fingerprint: str,
    claim_token: str,
    user_id: str | None,
    origin_class: str,
    ingest_quality: str = "unknown",
    content_sha256: str | None = None,
) -> None:
    """Atomically finalize identity, quality, origin, taint and account association.

    ``has_non_owner_source`` is a monotonic taint on Entity nodes. A derived or
    untrusted episode can therefore never silently become trusted merely because
    Graphiti merges its extracted entity with an owner-derived entity later.
    """
    if origin_class not in VALID_ORIGIN_CLASSES:
        raise ValueError(f"invalid origin_class: {origin_class!r}")

    if ingest_quality not in {"complete", "degraded", "unknown"}:
        raise ValueError("invalid ingest quality")
    if ingest_quality != "unknown" and (
        not isinstance(content_sha256, str) or len(content_sha256) != 64
        or any(ch not in "0123456789abcdef" for ch in content_sha256)
    ):
        raise ValueError("exact content digest required for known ingest quality")

    result = await graphiti.driver.execute_query(
        LOCK_ERASURE_CONTROL + """
        MATCH (e:Episodic {uuid:$episode_uuid})
        MATCH (c:FractalIngestClaim {claim_key:$claim_key, token:$claim_token, state:'PENDING'})
        WHERE c.episode_uuid=$episode_uuid
          AND c.erasure_revision=coalesce(gate.revision,0)
          AND coalesce(e.erasure_quarantined,false)=false
          AND coalesce(e.deleted,false)=false
          AND NOT EXISTS { MATCH (:FractalErasure {target_uuid:e.uuid}) }
        SET e.fingerprint=$fingerprint,
            e.group_id=$group_id,
            e.origin_class=$origin_class,
            e.ingest_quality=$ingest_quality,
            e.content_sha256=$content_sha256,
            e.ingest_event_id=$claim_token,
            e.source_identity_status='unknown',
            e.ingest_erasure_revision=c.erasure_revision,
            c.ingest_quality=$ingest_quality,
            c.content_sha256=$content_sha256,
            c.state='COMMITTED',
            c.committed_at=datetime()
        FOREACH (_ IN CASE WHEN $user_id IS NULL THEN [] ELSE [1] END |
            MERGE (u:User {user_id:$user_id})
            MERGE (e)-[:STORED_FOR]->(u)
        )
        FOREACH (_ IN CASE WHEN $user_id IS NOT NULL AND $origin_class='owner' THEN [1] ELSE [] END |
            MERGE (u:User {user_id:$user_id})
            MERGE (u)-[:AUTHORED]->(e)
        )
        WITH e
        OPTIONAL MATCH (e)-[:MENTIONS]->(n:Entity)
        FOREACH (_ IN CASE
            WHEN n IS NULL OR $origin_class = 'owner' THEN []
            ELSE [1]
        END | SET n.has_non_owner_source=true)
        RETURN e.uuid AS uuid
        """,
        episode_uuid=episode_uuid,
        claim_key=claim_key(group_id, fingerprint),
        claim_token=claim_token,
        fingerprint=fingerprint,
        group_id=group_id,
        user_id=user_id,
        origin_class=origin_class,
        ingest_quality=ingest_quality,
        content_sha256=content_sha256,
    )
    if not result.records:
        raise RuntimeError("episode finalization not admitted: missing target, claim or current erasure revision")
