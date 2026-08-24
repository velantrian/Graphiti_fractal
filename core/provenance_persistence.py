"""Exact-UUID persistence for non-authoritative provenance metadata.

Kept separate from ordinary episode metadata so provenance fields cannot broaden
or redefine the normal Graphiti episode write contract.
"""

from __future__ import annotations

PROVENANCE_FIELDS = {
    "provenance_id",
    "provenance_activity",
    "provenance_agent",
    "payload_sha256",
    "derived_source_ids",
    "authoritative_fact",
}


async def persist_provenance_metadata(graphiti, episode_uuid: str, metadata: dict) -> dict:
    if not episode_uuid:
        raise ValueError("episode_uuid is required")
    unknown = set(metadata) - PROVENANCE_FIELDS
    if unknown:
        raise ValueError(f"unsupported provenance metadata fields: {sorted(unknown)}")
    if metadata.get("authoritative_fact") not in (None, False):
        raise ValueError("derived provenance cannot mark an episode authoritative")
    if not metadata:
        return {"status": "unchanged", "episode_uuid": episode_uuid}

    normalized = dict(metadata)
    normalized["authoritative_fact"] = False
    params = {"uuid": episode_uuid, **normalized}
    assignments = ", ".join(f"e.{key} = ${key}" for key in normalized)
    result = await graphiti.driver.execute_query(
        f"MATCH (e:Episodic {{uuid:$uuid}}) SET {assignments} RETURN e.uuid AS uuid",
        **params,
    )
    if not result.records:
        raise LookupError(f"episode not found for provenance update: {episode_uuid}")
    return {"status": "updated", "episode_uuid": episode_uuid}
