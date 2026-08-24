"""App-level ingest claims and exact-UUID finalization.

This layer does not pretend to make Graphiti's internal write transaction part of
our transaction. It closes Fractal-side concurrent duplicate races and makes
post-Graphiti fingerprint/group/authorship finalization atomic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


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
    """Acquire one unique claim or return None when another live claim exists."""
    await ensure_ingest_claim_constraint(graphiti)
    key = claim_key(group_id, fingerprint)
    token = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=max(30, ttl_seconds))

    # Remove only stale, uncommitted claims before attempting CREATE.
    await graphiti.driver.execute_query(
        """
        MATCH (c:FractalIngestClaim {claim_key:$claim_key})
        WHERE c.state='PENDING' AND c.expires_at < datetime($now)
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
    await graphiti.driver.execute_query(
        """
        MATCH (c:FractalIngestClaim {claim_key:$claim_key, token:$token, state:'PENDING'})
        DELETE c
        """,
        claim_key=claim_key(group_id, fingerprint),
        token=token,
    )


async def finalize_episode_identity(
    graphiti,
    *,
    episode_uuid: str,
    group_id: str,
    fingerprint: str,
    claim_token: str,
    user_id: str | None,
) -> None:
    """Atomically finalize app-owned identity metadata and authorship by UUID."""
    result = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic {uuid:$episode_uuid})
        MATCH (c:FractalIngestClaim {claim_key:$claim_key, token:$claim_token, state:'PENDING'})
        SET e.fingerprint=$fingerprint,
            e.group_id=$group_id,
            c.state='COMMITTED',
            c.episode_uuid=$episode_uuid,
            c.committed_at=datetime()
        FOREACH (_ IN CASE WHEN $user_id IS NULL THEN [] ELSE [1] END |
            MERGE (u:User {user_id:$user_id})
            MERGE (u)-[:AUTHORED]->(e)
        )
        RETURN e.uuid AS uuid
        """,
        episode_uuid=episode_uuid,
        claim_key=claim_key(group_id, fingerprint),
        claim_token=claim_token,
        fingerprint=fingerprint,
        group_id=group_id,
        user_id=user_id,
    )
    if not result.records:
        raise RuntimeError("episode finalization failed: episode or matching ingest claim not found")
