"""Deterministic evaluation for Fractal retrieval modes.

This evaluator measures routing and context-shape behavior only. It does not
claim answer quality, factual truth, or provider performance. Those require a
separate corpus/provider-backed evaluation.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from time import perf_counter
from typing import Iterable

from core.graphrag_policy import RetrievalMode, apply_mode_weights, plan_retrieval
from core.types import SearchResult


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    query: str
    expected_mode: RetrievalMode
    required_collections: tuple[str, ...]
    forbidden_collections: tuple[str, ...] = ()


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        "local-decision",
        "What did I decide about the Reader pipeline?",
        RetrievalMode.LOCAL,
        ("episodes", "entities", "edges"),
        ("communities",),
    ),
    EvalCase(
        "global-themes",
        "What are the main themes across the whole corpus?",
        RetrievalMode.GLOBAL,
        ("communities",),
        ("episodes", "entities", "edges"),
    ),
    EvalCase(
        "drift-relationship",
        "Why are Crystal and Titan connected and what broader pattern explains it?",
        RetrievalMode.DRIFT,
        ("episodes", "entities", "edges", "communities"),
    ),
    EvalCase(
        "local-specific",
        "When was the Graphiti cleanup merged?",
        RetrievalMode.LOCAL,
        ("episodes", "entities", "edges"),
        ("communities",),
    ),
    EvalCase(
        "global-trends-ru",
        "Какие общие тенденции видны во всех проектах?",
        RetrievalMode.GLOBAL,
        ("communities",),
        ("episodes", "entities", "edges"),
    ),
    EvalCase(
        "drift-causal-ru",
        "Почему эти решения связаны и как они влияют друг на друга?",
        RetrievalMode.DRIFT,
        ("episodes", "entities", "edges", "communities"),
    ),
)


def _fixture() -> SearchResult:
    return SearchResult(
        episodes=[{"uuid": "ep1", "score": 0.90}, {"uuid": "ep2", "score": 0.70}],
        entities=[{"uuid": "en1", "score": 0.85}, {"uuid": "en2", "score": 0.65}],
        edges=[{"uuid": "ed1", "score": 0.80}, {"uuid": "ed2", "score": 0.60}],
        communities=[{"uuid": "co1", "score": 0.95}, {"uuid": "co2", "score": 0.75}],
        total_episodes=2,
        total_entities=2,
        total_edges=2,
        total_communities=2,
    )


def _nonempty_collections(result: SearchResult) -> set[str]:
    return {
        name
        for name in ("episodes", "entities", "edges", "communities")
        if getattr(result, name)
    }


def evaluate_case(case: EvalCase, requested_mode: str = "auto") -> dict:
    started = perf_counter()
    plan = plan_retrieval(case.query, requested_mode)
    result = apply_mode_weights(deepcopy(_fixture()), plan)
    elapsed_ms = (perf_counter() - started) * 1000
    present = _nonempty_collections(result)

    routing_correct = plan.effective_mode is case.expected_mode if requested_mode == "auto" else True
    required_coverage = (
        sum(1 for name in case.required_collections if name in present) / len(case.required_collections)
        if case.required_collections else 1.0
    )
    forbidden_leakage = (
        sum(1 for name in case.forbidden_collections if name in present) / len(case.forbidden_collections)
        if case.forbidden_collections else 0.0
    )
    shape_score = max(0.0, required_coverage - forbidden_leakage)

    return {
        "case_id": case.case_id,
        "query": case.query,
        "requested_mode": requested_mode,
        "effective_mode": plan.effective_mode.value,
        "expected_mode": case.expected_mode.value,
        "routing_correct": routing_correct,
        "required_coverage": round(required_coverage, 4),
        "forbidden_leakage": round(forbidden_leakage, 4),
        "shape_score": round(shape_score, 4),
        "present_collections": sorted(present),
        "policy_latency_ms": round(elapsed_ms, 4),
        "authoritative": plan.authoritative,
        "writes_performed": plan.writes_performed,
    }


def evaluate_suite(cases: Iterable[EvalCase] = CASES) -> dict:
    rows = [evaluate_case(case, "auto") for case in cases]
    count = len(rows)
    routing_accuracy = sum(1 for row in rows if row["routing_correct"]) / count if count else 0.0
    mean_shape = sum(row["shape_score"] for row in rows) / count if count else 0.0
    mean_latency = sum(row["policy_latency_ms"] for row in rows) / count if count else 0.0
    return {
        "suite": "retrieval-mode-policy-v1",
        "case_count": count,
        "routing_accuracy": round(routing_accuracy, 4),
        "mean_shape_score": round(mean_shape, 4),
        "mean_policy_latency_ms": round(mean_latency, 4),
        "rows": rows,
        "claims": {
            "measures_answer_quality": False,
            "measures_truth": False,
            "measures_live_db_latency": False,
            "measures_policy_routing": True,
            "measures_context_shape": True,
        },
    }
