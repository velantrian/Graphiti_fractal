"""Preview-first external memory import.

Imported material is isolated in the `imports` namespace and treated as
untrusted input. Importing does not grant durable-promotion authority.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from knowledge.ingest import ingest_text_document

MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ENTRIES = 5000
IMPORT_GROUP_ID = "imports"


def _read_bounded(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_IMPORT_BYTES:
        raise ValueError(f"import file exceeds {MAX_IMPORT_BYTES} bytes")
    return path.read_text(encoding="utf-8")


def _extract_json_text(value: Any) -> list[str]:
    """Extract human-readable text conservatively from common export shapes."""
    texts: list[str] = []
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            texts.append(cleaned)
    elif isinstance(value, list):
        for item in value:
            texts.extend(_extract_json_text(item))
    elif isinstance(value, dict):
        # Prefer common content-bearing fields before recursively inspecting values.
        preferred = ["content", "text", "message", "prompt", "response", "summary"]
        matched = False
        for key in preferred:
            if key in value:
                matched = True
                texts.extend(_extract_json_text(value[key]))
        if not matched:
            for item in value.values():
                texts.extend(_extract_json_text(item))
    return texts


def build_import_plan(path: str, *, source_type: str = "auto") -> dict:
    """Parse one export into a bounded, side-effect-free import plan."""
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(target)

    raw = _read_bounded(target)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    suffix = target.suffix.lower()
    detected = source_type if source_type != "auto" else suffix.lstrip(".") or "text"

    if suffix == ".json":
        texts = _extract_json_text(json.loads(raw))
    elif suffix == ".jsonl":
        texts = []
        for line_number, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                texts.extend(_extract_json_text(json.loads(line)))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}") from exc
    else:
        texts = [raw.strip()] if raw.strip() else []

    # Preserve order while removing exact duplicate snippets from the plan.
    deduped: list[str] = []
    seen: set[str] = set()
    for text in texts:
        cleaned = text.strip()
        if not cleaned:
            continue
        key = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
        if len(deduped) >= MAX_IMPORT_ENTRIES:
            break

    return {
        "mode": "PREVIEW",
        "source_path": str(target),
        "source_type": detected,
        "source_sha256": digest,
        "origin_class": "untrusted",
        "target_group_id": IMPORT_GROUP_ID,
        "entry_count": len(deduped),
        "entries": [
            {
                "index": index,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "chars": len(text),
                "preview": text[:240],
            }
            for index, text in enumerate(deduped, start=1)
        ],
        "_payload": deduped,
        "writes_performed": False,
    }


async def apply_import_plan(memory, plan: dict, *, apply: bool = False) -> dict:
    """Apply a previously built plan only when explicitly requested."""
    public_plan = {key: value for key, value in plan.items() if key != "_payload"}
    if not apply:
        return public_plan
    if plan.get("origin_class") != "untrusted":
        raise ValueError("external imports must enter as origin_class=untrusted")
    if plan.get("target_group_id") != IMPORT_GROUP_ID:
        raise ValueError("external imports must enter the isolated imports namespace")

    added = 0
    skipped = 0
    warnings: list[str] = []
    source = (
        f"external_import:{plan.get('source_type', 'unknown')}:"
        f"{str(plan.get('source_sha256', ''))[:16]}"
    )
    for text in plan.get("_payload", []):
        # Bypass the owner-default convenience wrapper so import provenance is
        # classified before Graphiti entity finalization, not repaired afterwards.
        result = await ingest_text_document(
            memory.graphiti,
            text,
            source_description=source,
            user_id=memory.user_id,
            group_id=IMPORT_GROUP_ID,
            origin_class="untrusted",
        )
        added += int(result.get("added", 0))
        skipped += int(result.get("skipped", 0))
        warnings.extend(result.get("warnings", []))

    return {
        **public_plan,
        "mode": "APPLIED",
        "added": added,
        "skipped": skipped,
        "warnings": warnings,
        "writes_performed": True,
        "promotion_authorized": False,
    }
