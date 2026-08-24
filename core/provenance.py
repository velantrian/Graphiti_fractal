"""Deterministic provenance identities for derived Fractal artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable


def provenance_id(*, kind: str, source_ids: Iterable[str], activity: str, payload_digest: str = "") -> str:
    sources = sorted({str(value).strip() for value in source_ids if str(value).strip()})
    if not kind.strip() or not activity.strip() or not sources:
        raise ValueError("kind, activity and at least one source id are required")
    canonical = json.dumps(
        {
            "kind": kind.strip(),
            "activity": activity.strip(),
            "source_ids": sources,
            "payload_digest": payload_digest.strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"prov:{kind.strip().lower()}:{digest}"


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_provenance_record(
    *,
    kind: str,
    source_ids: Iterable[str],
    activity: str,
    agent: str,
    payload: str = "",
) -> dict:
    source_list = sorted({str(value).strip() for value in source_ids if str(value).strip()})
    pid = provenance_id(
        kind=kind,
        source_ids=source_list,
        activity=activity,
        payload_digest=text_digest(payload) if payload else "",
    )
    return {
        "provenance_id": pid,
        "kind": kind,
        "source_ids": source_list,
        "activity": activity,
        "agent": agent,
        "payload_sha256": text_digest(payload) if payload else None,
        "authoritative_fact": False,
    }
