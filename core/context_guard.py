"""Bounded context receipts and recall-derived turn metadata.

These helpers make memory use observable without creating a second memory,
retrieval, evidence, or truth authority. A receipt describes context exposure;
it is not evidence. Recall-derived metadata prevents model repetition from being
mistaken for an independent confirmation signal.
"""

from __future__ import annotations

import hashlib

from core.recall_telemetry import query_fingerprint
from core.types import ContextReceipt, ContextResult


def build_context_receipt(
    *,
    query: str,
    context: ContextResult,
    requested_mode: str = "auto",
    effective_mode: str = "unknown",
    reason: str = "",
    status: str = "OK",
    max_tokens: int = 0,
    source_ids: list[str] | None = None,
) -> ContextReceipt:
    """Describe the exact rendered context text exposed to the model.

    Exact rendered source UUIDs are taken from ``ContextResult.source_ids`` by
    default. Callers may still supply an explicit list for compatibility, but the
    receipt never invents UUIDs that the formatter did not expose.
    """
    text = context.text or ""
    raw_ids = context.source_ids if source_ids is None else source_ids
    normalized_ids = sorted({str(value) for value in (raw_ids or []) if str(value).strip()})

    receipt_status = status
    receipt_reason = reason
    if status == "OK" and context.failed_scopes:
        receipt_status = "DEGRADED_PARTIAL"
        failed = ", ".join(context.failed_scopes)
        receipt_reason = f"{reason}; failed scopes: {failed}" if reason else f"failed scopes: {failed}"

    return ContextReceipt(
        status=receipt_status,
        requested_mode=requested_mode,
        effective_mode=effective_mode,
        reason=receipt_reason,
        source_ids=normalized_ids,
        source_counts=dict(context.sources or {}),
        query_sha256=query_fingerprint(query),
        context_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        max_tokens=int(max_tokens),
        token_estimate=int(context.token_estimate or 0),
        truncated="[Контекст обрезан по лимиту]" in text,
        authoritative=False,
        writes_performed=False,
    )


def degraded_context(*, query: str, status: str, reason: str, max_tokens: int) -> ContextResult:
    """Return an empty fail-soft context with an auditable non-authoritative receipt."""
    empty = ContextResult(
        text="",
        token_estimate=0,
        sources={"episodes": 0, "entities": 0, "edges": 0, "communities": 0},
    )
    empty.receipt = build_context_receipt(
        query=query,
        context=empty,
        requested_mode="auto",
        effective_mode="none",
        reason=reason,
        status=status,
        max_tokens=max_tokens,
    )
    return empty


async def persist_recall_guard_metadata(
    graphiti,
    *,
    episode_uuid: str,
    context_receipt: ContextReceipt | None,
) -> dict:
    """Mark a persisted chat turn as recall-derived by exact UUID.

    This metadata never changes truth/confidence/validity. It only records that
    the assistant response was produced while non-empty recalled context was
    present.
    """
    derived = bool(
        context_receipt
        and context_receipt.status in {"OK", "DEGRADED_PARTIAL"}
        and context_receipt.token_estimate > 0
        and any(int(value) > 0 for value in context_receipt.source_counts.values())
    )
    digest = context_receipt.context_sha256 if derived and context_receipt else None
    query_hash = context_receipt.query_sha256 if context_receipt else None
    result = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic {uuid:$uuid})
        SET e.recall_derived=$recall_derived,
            e.recall_context_sha256=$context_sha256,
            e.recall_query_sha256=$query_sha256,
            e.recall_guard_authoritative=false
        RETURN e.uuid AS uuid
        """,
        uuid=episode_uuid,
        recall_derived=derived,
        context_sha256=digest,
        query_sha256=query_hash,
    )
    if not result.records:
        raise LookupError(f"episode not found for recall guard metadata: {episode_uuid}")
    return {
        "episode_uuid": episode_uuid,
        "recall_derived": derived,
        "authoritative": False,
    }
