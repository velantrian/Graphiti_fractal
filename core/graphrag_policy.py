"""Bounded GraphRAG-inspired retrieval policy.

This module adds routing and synthesis policy only. It does not introduce a
second graph, database, ingestion path, or truth authority. All retrieval stays
on the existing Graphiti + Neo4j memory substrate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RetrievalMode(str, Enum):
    AUTO = "auto"
    LOCAL = "local"
    GLOBAL = "global"
    DRIFT = "drift"


@dataclass(frozen=True)
class RetrievalPlan:
    requested_mode: RetrievalMode
    effective_mode: RetrievalMode
    use_episodes: bool
    use_entities: bool
    use_edges: bool
    use_communities: bool
    community_weight: float
    local_weight: float
    reason: str
    authoritative: bool = False
    writes_performed: bool = False


_GLOBAL_CUES = (
    "overall", "across", "whole", "global", "main themes", "themes",
    "patterns", "trends", "entire", "corpus", "общ", "в целом", "темы",
    "тенденц", "по всему", "во всех",
)

_DRIFT_CUES = (
    "why", "how", "relationship", "connect", "compare", "impact", "because",
    "почему", "как", "связ", "сравн", "влиян", "причин",
)


def _coerce_mode(mode: str | RetrievalMode | None) -> RetrievalMode:
    if isinstance(mode, RetrievalMode):
        return mode
    value = (mode or "auto").strip().lower()
    try:
        return RetrievalMode(value)
    except ValueError as exc:
        raise ValueError("retrieval mode must be auto|local|global|drift") from exc


def plan_retrieval(query: str, mode: str | RetrievalMode | None = None) -> RetrievalPlan:
    """Choose a bounded read-side mode using deterministic query cues."""
    requested = _coerce_mode(mode)
    normalized = " ".join((query or "").lower().split())
    if not normalized:
        raise ValueError("query is empty")

    effective = requested
    reason = f"explicit_{requested.value}"
    if requested is RetrievalMode.AUTO:
        if any(cue in normalized for cue in _GLOBAL_CUES):
            effective = RetrievalMode.GLOBAL
            reason = "auto_global_corpus_or_theme_cue"
        elif any(cue in normalized for cue in _DRIFT_CUES):
            effective = RetrievalMode.DRIFT
            reason = "auto_drift_relational_or_explanatory_cue"
        else:
            effective = RetrievalMode.LOCAL
            reason = "auto_local_default"

    if effective is RetrievalMode.LOCAL:
        return RetrievalPlan(requested, effective, True, True, True, False, 0.0, 1.0, reason)
    if effective is RetrievalMode.GLOBAL:
        return RetrievalPlan(requested, effective, False, False, False, True, 1.0, 0.0, reason)
    if effective is RetrievalMode.DRIFT:
        return RetrievalPlan(requested, effective, True, True, True, True, 0.45, 0.55, reason)
    raise AssertionError("AUTO must resolve to a concrete retrieval mode")


def apply_mode_weights(result, plan: RetrievalPlan):
    """Reweight an existing SearchResult without creating new evidence.

    The underlying Graphiti scores remain retrieval scores. This function only
    changes application-side ranking for context assembly.
    """
    for collection_name in ("episodes", "entities", "edges"):
        collection = getattr(result, collection_name, [])
        if not plan.use_episodes and collection_name == "episodes":
            collection.clear()
            continue
        if not plan.use_entities and collection_name == "entities":
            collection.clear()
            continue
        if not plan.use_edges and collection_name == "edges":
            collection.clear()
            continue
        for item in collection:
            item["score"] = float(item.get("score", 0.0)) * plan.local_weight
        collection.sort(key=lambda item: item.get("score", 0.0), reverse=True)

    if not plan.use_communities:
        result.communities.clear()
    else:
        for item in result.communities:
            item["score"] = float(item.get("score", 0.0)) * plan.community_weight
        result.communities.sort(key=lambda item: item.get("score", 0.0), reverse=True)

    result.total_episodes = len(result.episodes)
    result.total_entities = len(result.entities)
    result.total_edges = len(result.edges)
    result.total_communities = len(result.communities)
    return result
