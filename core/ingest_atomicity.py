"""App-level ingest claims and exact-UUID finalization.

This layer does not pretend to make Graphiti's internal write transaction part of
our transaction. It closes Fractal-side concurrent duplicate races and makes
post-Graphiti fingerprint/group/authorship/origin finalization atomic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


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
) -> str | None:
    """Acquire one unique claim or return None when another claim owns identity."""
    await ensure_ingest_claim_constraint(graphiti)
    key = claim_key(group_id, fingerprint)
    token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=max(30, ttl_seconds))

    # Only abandoned PENDING claims with no created episode may expire. Once an
    # episode UUID exists, the claim remains fail-closed for explicit recovery.
    await graphiti.driver.execute_query(
        """
        MATCH (c:FractalIngestClaim {claim_key:$claim_key})
        WHERE c.state='PENDING'
          AND c.episode_uuid IS NULL
          AND c.expires_at < datetime($now)
        DELETE c
        """,
        claim_key=key,
        now=now.isoformat(),
    )
    try:
        result = await graphiti.driver.execute_query(
            """
            CREATE (c:FractalIngestClaim {
                claim_key:$claim_key,
                group_id:$group_id,
                fingerprint:$fingerprint,
                token:$token,
                state:'PENDING',
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
        )
        return str(result.records[0]["token"]) if result.records else None
    except Exception:  # uniqueness conflict => another claim owns this identity
        return None


async def release_ingest_claim(
    graphiti,
    *,
    group_id: str,
    fingerprint: str,
    token: str,
) -> None:
    """Release only a claim for which no Graphiti episode was created."""
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
    await graphiti.driver.execute_query(
        """
        MATCH (c:FractalIngestClaim {claim_key:$claim_key, token:$token})
        SET c.state='FAILED', c.failed_at=datetime(), c.error_type=$error_type
        """,
        claim_key=claim_key(group_id, fingerprint),
        token=token,
        error_type=error_type[:120],
    )


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
) -> None:
    """Atomically finalize app-owned identity, origin, taint, and authorship.

    ``has_non_owner_source`` is a monotonic taint on Entity nodes. A derived or
    untrusted episode can therefore never silently become trusted merely because
    Graphiti merges its extracted entity with an owner-derived entity later.
    """
    if origin_class not in VALID_ORIGIN_CLASSES:
        raise ValueError(f"invalid origin_class: {origin_class!r}")

    result = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic {uuid:$episode_uuid})
        MATCH (c:FractalIngestClaim {claim_key:$claim_key, token:$claim_token, state:'PENDING'})
        WHERE c.episode_uuid=$episode_uuid
        SET e.fingerprint=$fingerprint,
            e.group_id=$group_id,
            e.origin_class=$origin_class,
            c.state='COMMITTED',
            c.committed_at=datetime()
        FOREACH (_ IN CASE WHEN $user_id IS NULL THEN [] ELSE [1] END |
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
    )
    if not result.records:
        raise RuntimeError("episode finalization failed: episode or matching ingest claim not found")
