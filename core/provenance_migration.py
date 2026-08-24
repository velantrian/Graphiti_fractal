"""Fail-closed provenance migration planning for legacy derived episodes."""

from __future__ import annotations

from core.provenance import build_provenance_record
from core.provenance_persistence import persist_provenance_metadata


def plan_legacy_artifact(record: dict) -> dict:
    uuid = str(record.get("uuid") or "")
    kind = str(record.get("episode_kind") or "")
    content = str(record.get("content") or "")
    existing = record.get("provenance_id")
    if existing:
        return {"uuid": uuid, "kind": kind, "status": "ALREADY_MIGRATED", "writes_performed": False}

    if kind == "chat_summary":
        source_ids = [str(value) for value in (record.get("summarized_turns") or []) if str(value)]
        if not source_ids:
            return {"uuid": uuid, "kind": kind, "status": "BLOCKED_MISSING_SOURCE_IDS", "writes_performed": False}
        provenance = build_provenance_record(kind="chat_summary", source_ids=source_ids, activity="chat_summary_synthesis", agent="fractal:summary", payload=content)
    elif kind == "l3_profile":
        source_ids = [str(value) for value in (record.get("derived_source_ids") or []) if str(value)]
        if not source_ids:
            return {"uuid": uuid, "kind": kind, "status": "BLOCKED_MISSING_SOURCE_IDS", "writes_performed": False}
        provenance = build_provenance_record(kind="l3_profile", source_ids=source_ids, activity="l3_semantic_synthesis", agent="fractal:l3", payload=content)
    else:
        return {"uuid": uuid, "kind": kind, "status": "OUT_OF_SCOPE", "writes_performed": False}

    return {"uuid": uuid, "kind": kind, "status": "READY", "source_ids": source_ids, "provenance": provenance, "writes_performed": False}


async def scan_legacy_derived(graphiti) -> list[dict]:
    result = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic)
        WHERE e.episode_kind IN ['chat_summary','l3_profile']
        RETURN e.uuid AS uuid,
               e.episode_kind AS episode_kind,
               coalesce(e.content,e.episode_body,'') AS content,
               e.summarized_turns AS summarized_turns,
               e.derived_source_ids AS derived_source_ids,
               e.provenance_id AS provenance_id
        ORDER BY coalesce(e.created_at,e.valid_at) ASC
        """
    )
    return [plan_legacy_artifact(dict(record)) for record in result.records]


async def apply_ready_plan(graphiti, plan: dict) -> dict:
    if plan.get("status") != "READY":
        raise ValueError("only READY provenance migration plans may be applied")
    provenance = plan["provenance"]
    await persist_provenance_metadata(graphiti, plan["uuid"], {
        "provenance_id": provenance["provenance_id"],
        "provenance_activity": provenance["activity"],
        "provenance_agent": provenance["agent"],
        "payload_sha256": provenance["payload_sha256"],
        "derived_source_ids": plan["source_ids"],
        "authoritative_fact": False,
    })
    return {**plan, "status": "APPLIED", "writes_performed": True}
