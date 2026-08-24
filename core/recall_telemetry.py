"""Operational recall telemetry kept separate from memory authority.

Telemetry records usage of retrieved object UUIDs and hashed query identity.
It never changes Graphiti facts, confidence, validity, or provenance.
"""

from __future__ import annotations

import hashlib
from typing import Iterable


def query_fingerprint(query: str) -> str:
    normalized = " ".join(query.strip().lower().split())
    if not normalized:
        raise ValueError("query is empty")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def ensure_recall_constraints(graphiti) -> None:
    await graphiti.driver.execute_query(
        """
        CREATE CONSTRAINT recall_telemetry_unique IF NOT EXISTS
        FOR (r:RecallTelemetry)
        REQUIRE (r.user_id, r.object_uuid) IS UNIQUE
        """
    )
    await graphiti.driver.execute_query(
        """
        CREATE CONSTRAINT recall_query_unique IF NOT EXISTS
        FOR (q:RecallQuery)
        REQUIRE (q.user_id, q.query_hash) IS UNIQUE
        """
    )


async def record_recall(graphiti, *, user_id: str, query: str, object_uuids: Iterable[str]) -> dict:
    """Record bounded usage metadata for retrieved objects.

    Query text is not persisted; only SHA-256 fingerprint is stored.
    """
    if not user_id:
        raise ValueError("user_id is required")
    uuids = sorted({str(value) for value in object_uuids if str(value).strip()})
    if not uuids:
        return {"recorded": 0, "query_hash": None, "authoritative": False}
    qhash = query_fingerprint(query)
    await ensure_recall_constraints(graphiti)
    result = await graphiti.driver.execute_query(
        """
        MERGE (q:RecallQuery {user_id:$user_id, query_hash:$query_hash})
        ON CREATE SET q.first_seen_at=datetime(), q.count=0
        SET q.count=q.count+1, q.last_seen_at=datetime()
        WITH q
        UNWIND $object_uuids AS object_uuid
        MERGE (r:RecallTelemetry {user_id:$user_id, object_uuid:object_uuid})
        ON CREATE SET r.recall_count=0, r.first_recalled_at=datetime()
        SET r.recall_count=r.recall_count+1, r.last_recalled_at=datetime()
        MERGE (r)-[seen:SEEN_IN_QUERY]->(q)
        ON CREATE SET seen.first_seen_at=datetime(), seen.count=0
        SET seen.count=seen.count+1, seen.last_seen_at=datetime()
        RETURN count(r) AS recorded
        """,
        user_id=user_id,
        query_hash=qhash,
        object_uuids=uuids,
    )
    recorded = int(result.records[0]["recorded"]) if result.records else 0
    return {"recorded": recorded, "query_hash": qhash, "authoritative": False}


async def read_recall_signals(graphiti, *, user_id: str, object_uuid: str) -> dict:
    result = await graphiti.driver.execute_query(
        """
        MATCH (r:RecallTelemetry {user_id:$user_id, object_uuid:$object_uuid})
        OPTIONAL MATCH (r)-[:SEEN_IN_QUERY]->(q:RecallQuery {user_id:$user_id})
        RETURN coalesce(r.recall_count,0) AS recall_count,
               count(DISTINCT q.query_hash) AS unique_queries,
               r.first_recalled_at AS first_recalled_at,
               r.last_recalled_at AS last_recalled_at
        """,
        user_id=user_id,
        object_uuid=object_uuid,
    )
    if not result.records:
        return {
            "recall_count": 0,
            "unique_queries": 0,
            "first_recalled_at": None,
            "last_recalled_at": None,
            "authoritative": False,
        }
    record = result.records[0]
    return {
        "recall_count": int(record["recall_count"] or 0),
        "unique_queries": int(record["unique_queries"] or 0),
        "first_recalled_at": record["first_recalled_at"],
        "last_recalled_at": record["last_recalled_at"],
        "authoritative": False,
    }
