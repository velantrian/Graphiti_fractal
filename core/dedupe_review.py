"""Pure read-only candidate classification; never infer deletion authority."""
from __future__ import annotations

from collections import defaultdict
from core.text_utils import fingerprint


def episode_candidate_report(episodes: list[dict]) -> dict:
    groups = defaultdict(list)
    excluded = []
    for item in episodes:
        uid, group = item.get("uuid"), item.get("group_id")
        body = str(item.get("text") or "").strip()
        if not uid or not group or group == "unknown" or not body:
            excluded.append({"uuid": uid, "reason": "MISSING_ID_GROUP_OR_BODY"})
            continue
        groups[(group, fingerprint(body))].append(item)
    candidates = []
    for (scope, digest), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        candidates.append({
            "group_id": scope, "body_fingerprint": digest,
            "uuids": sorted(str(it["uuid"]) for it in items),
            "status": "REVIEW_ONLY", "authorized_to_delete": False,
            "warning": "equal body does not prove equal provenance, time or graph links",
        })
    return {"status": "READ_ONLY", "writes_performed": False,
            "candidate_groups": candidates, "excluded": excluded}


def entity_candidate_report(entities: list[dict]) -> dict:
    groups = defaultdict(list)
    for item in entities:
        if item.get("uuid") and item.get("group_id") and item.get("normalized_name"):
            groups[(item["group_id"], item["normalized_name"])].append(item["uuid"])
    candidates = [{"group_id": group, "normalized_name": name, "uuids": sorted(ids),
                   "status": "REVIEW_ONLY", "authorized_to_merge": False}
                  for (group, name), ids in sorted(groups.items()) if len(ids) > 1]
    return {"status": "READ_ONLY", "writes_performed": False, "candidate_groups": candidates}
