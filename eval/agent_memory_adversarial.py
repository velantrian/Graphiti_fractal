"""Deterministic adversarial evaluation helpers for Fractal agent memory.

These helpers are evaluation-only. They do not write to Graphiti/Neo4j, change
retrieval, validate lessons, or grant promotion authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from core.memory_lifecycle import PromotionSignals, explain_promotion


@dataclass(frozen=True)
class EnvironmentProfile:
    environment_id: str
    available_tools: frozenset[str] = frozenset()
    forbidden_operations: frozenset[str] = frozenset()
    provider_capabilities: frozenset[str] = frozenset()
    stack: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ExperienceObservation:
    run_id: str
    status: str
    strategy_marker: str | None = None
    required_tools: frozenset[str] = frozenset()
    required_capabilities: frozenset[str] = frozenset()
    operation: str | None = None
    evaluator_id: str | None = None


def classify_experience(observation: ExperienceObservation) -> dict:
    """Classify a stored run without inferring causal lesson validity."""
    return {
        "run_id": observation.run_id,
        "status": observation.status,
        "strategy_marker": observation.strategy_marker,
        "classification": "OBSERVED_SUCCESS" if observation.status == "success" else "OBSERVED_NON_SUCCESS",
        "causal_validation": False,
        "validated_lesson": False,
        "evaluator_id": observation.evaluator_id,
        "authoritative": False,
        "writes_performed": False,
    }


def evaluate_environment_applicability(
    observation: ExperienceObservation,
    source: EnvironmentProfile,
    target: EnvironmentProfile,
) -> dict:
    """Detect whether a recalled success depends on capabilities absent at target."""
    missing_tools = sorted(observation.required_tools - target.available_tools)
    missing_capabilities = sorted(observation.required_capabilities - target.provider_capabilities)
    forbidden = bool(observation.operation and observation.operation in target.forbidden_operations)
    same_environment = source.environment_id == target.environment_id
    applicable = not missing_tools and not missing_capabilities and not forbidden
    return {
        "run_id": observation.run_id,
        "source_environment": source.environment_id,
        "target_environment": target.environment_id,
        "same_environment": same_environment,
        "missing_tools": missing_tools,
        "missing_capabilities": missing_capabilities,
        "operation_forbidden": forbidden,
        "applicable": applicable,
        "requires_warning": not applicable or not same_environment,
        "principle": "semantic_similarity!=operational_applicability",
        "authoritative": False,
        "writes_performed": False,
    }


def evaluate_promotion_contagion(
    *,
    origin_class: str,
    recall_counts: Iterable[int],
    unique_query_counts: Iterable[int],
    strategy_independently_validated: bool = False,
) -> dict:
    """Measure whether repeated reuse can create promotion pressure by itself.

    The evaluator intentionally calls the current side-effect-free promotion
    policy and reports when candidate eligibility appears without independent
    validation. It does not alter that policy or perform promotion.
    """
    recalls = list(recall_counts)
    uniques = list(unique_query_counts)
    if len(recalls) != len(uniques):
        raise ValueError("recall_counts and unique_query_counts must have equal length")

    rows = []
    for index, (recall_count, unique_queries) in enumerate(zip(recalls, uniques), start=1):
        # Hold non-frequency signals high to isolate whether repeated reuse can
        # eventually satisfy the existing deterministic candidate gates.
        signals = PromotionSignals(
            relevance=1.0,
            frequency=min(1.0, recall_count / 10.0),
            query_diversity=min(1.0, unique_queries / 10.0),
            recency=1.0,
            consolidation=1.0,
            conceptual_richness=1.0,
        )
        decision = explain_promotion(
            signals,
            origin_class=origin_class,
            recall_count=recall_count,
            unique_queries=unique_queries,
        )
        rows.append({
            "round": index,
            "recall_count": recall_count,
            "unique_queries": unique_queries,
            "decision": decision["decision"],
            "score": decision["score"],
            "writes_performed": decision["writes_performed"],
        })

    candidate_without_validation = any(
        row["decision"] == "PROMOTE_CANDIDATE" for row in rows
    ) and not strategy_independently_validated

    return {
        "origin_class": origin_class,
        "independently_validated": strategy_independently_validated,
        "rows": rows,
        "promotion_pressure_without_validation": candidate_without_validation,
        "durable_promotion_performed": False,
        "authoritative": False,
        "writes_performed": False,
    }


def evaluate_task_order_runs(runs: dict[str, list[float]]) -> dict:
    """Summarize score variance across ordinal/shuffled task orders."""
    if not runs:
        raise ValueError("runs must not be empty")
    means = {
        order: sum(scores) / len(scores)
        for order, scores in runs.items()
        if scores
    }
    if not means:
        raise ValueError("each run order must contain at least one score")
    best_order = max(means, key=means.get)
    worst_order = min(means, key=means.get)
    return {
        "means": {key: round(value, 6) for key, value in means.items()},
        "best_order": best_order,
        "worst_order": worst_order,
        "best_worst_gap": round(means[best_order] - means[worst_order], 6),
        "order_sensitive": len({round(value, 6) for value in means.values()}) > 1,
        "authoritative": False,
        "writes_performed": False,
    }
