"""Side-effect-free derived graph contracts for Fractal Memory.

These helpers model causal hypotheses, provenance, evidence topology and graph
analytics plans without mutating Graphiti/Neo4j. Derived analysis is never a
memory-authority write path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable


class DerivedStatus(str, Enum):
    HYPOTHESIS = "HYPOTHESIS"
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvidenceRelation(str, Enum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    REFINES = "REFINES"
    SUPERSEDES = "SUPERSEDES"


@dataclass(frozen=True)
class ConfidenceProfile:
    source_quality: float = 0.0
    evidence_count: float = 0.0
    independence: float = 0.0
    recency: float = 0.0
    causal_strength: float = 0.0
    contradiction_penalty: float = 0.0

    def normalized(self) -> dict[str, float]:
        values = asdict(self)
        return {
            key: min(1.0, max(0.0, float(value)))
            for key, value in values.items()
        }

    def score(self) -> float:
        values = self.normalized()
        positive = (
            values["source_quality"] * 0.24
            + values["evidence_count"] * 0.18
            + values["independence"] * 0.18
            + values["recency"] * 0.12
            + values["causal_strength"] * 0.28
        )
        penalty = values["contradiction_penalty"] * 0.35
        return round(max(0.0, min(1.0, positive - penalty)), 6)


@dataclass(frozen=True)
class CausalHypothesis:
    cause_id: str
    effect_id: str
    method: str
    confidence: ConfidenceProfile
    status: DerivedStatus = DerivedStatus.HYPOTHESIS
    lag: str | None = None
    confounders: tuple[str, ...] = ()
    supporting_episode_ids: tuple[str, ...] = ()
    contradicting_episode_ids: tuple[str, ...] = ()


def explain_causal_hypothesis(hypothesis: CausalHypothesis) -> dict:
    """Return a transparent causal-hypothesis representation with no writes."""
    if not hypothesis.cause_id or not hypothesis.effect_id:
        raise ValueError("cause_id and effect_id are required")
    if hypothesis.cause_id == hypothesis.effect_id:
        raise ValueError("cause_id and effect_id must differ")
    if not hypothesis.method.strip():
        raise ValueError("causal method/assumption source is required")

    return {
        "kind": "CAUSAL_HYPOTHESIS",
        "cause_id": hypothesis.cause_id,
        "effect_id": hypothesis.effect_id,
        "method": hypothesis.method,
        "status": hypothesis.status.value,
        "confidence": hypothesis.confidence.score(),
        "confidence_profile": hypothesis.confidence.normalized(),
        "lag": hypothesis.lag,
        "confounders": list(hypothesis.confounders),
        "supported_by": list(hypothesis.supporting_episode_ids),
        "contradicted_by": list(hypothesis.contradicting_episode_ids),
        "authoritative_fact": False,
        "writes_performed": False,
    }


def build_provenance_lineage(
    *,
    derived_id: str,
    source_episode_ids: Iterable[str],
    activity: str,
    agent: str,
) -> dict:
    """Build a W3C-PROV-inspired lineage record without persisting it."""
    sources = [str(value) for value in source_episode_ids if str(value).strip()]
    if not derived_id or not activity or not agent:
        raise ValueError("derived_id, activity and agent are required")
    if not sources:
        raise ValueError("at least one source episode is required")
    return {
        "kind": "PROVENANCE_LINEAGE",
        "entity": derived_id,
        "wasDerivedFrom": sources,
        "wasGeneratedBy": activity,
        "wasAssociatedWith": agent,
        "writes_performed": False,
    }


def build_evidence_topology(
    *,
    claim_id: str,
    relations: Iterable[tuple[str | EvidenceRelation, str]],
) -> dict:
    """Normalize support/contradiction/refinement links for a derived claim."""
    if not claim_id:
        raise ValueError("claim_id is required")
    edges = []
    for relation, target_id in relations:
        rel = EvidenceRelation(relation)
        if not target_id:
            raise ValueError("evidence target id is required")
        edges.append({"relation": rel.value, "target_id": str(target_id)})
    return {
        "kind": "EVIDENCE_TOPOLOGY",
        "claim_id": claim_id,
        "edges": edges,
        "authoritative_fact": False,
        "writes_performed": False,
    }


ALLOWED_GDS_MODES = {"stream", "stats"}
BLOCKED_GDS_MODES = {"mutate", "write"}


def plan_gds_analysis(*, algorithm: str, mode: str = "stream", projection: str = "ephemeral") -> dict:
    """Plan read-side Neo4j GDS analysis; writes/mutations are fail-closed."""
    normalized_mode = mode.strip().lower()
    if normalized_mode in BLOCKED_GDS_MODES:
        raise ValueError("GDS mutate/write modes are not allowed by Fractal derived-analysis policy")
    if normalized_mode not in ALLOWED_GDS_MODES:
        raise ValueError(f"unsupported GDS mode: {mode!r}")
    if not algorithm.strip():
        raise ValueError("algorithm is required")
    return {
        "kind": "GDS_ANALYSIS_PLAN",
        "algorithm": algorithm.strip(),
        "mode": normalized_mode,
        "projection": projection,
        "ephemeral": projection == "ephemeral",
        "results_authoritative": False,
        "writes_performed": False,
    }
