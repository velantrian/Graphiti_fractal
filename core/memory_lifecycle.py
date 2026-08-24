"""Bounded memory lifecycle policies inspired by external agent-memory systems.

This module is intentionally policy-only: scoring or consolidation planning never
writes to Graphiti/Neo4j. Persistence remains an explicit caller decision.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable


class RecallMode(str, Enum):
    OFF = "off"
    AUTO = "auto"
    ALWAYS = "always"


class OriginClass(str, Enum):
    OWNER = "owner"
    AGENT_DERIVED = "agent_derived"
    UNTRUSTED = "untrusted"
    SYSTEM = "system"


TRIVIAL_CHAT = {
    "привет", "здравствуй", "здравствуйте", "спасибо", "ок", "окей",
    "hello", "hi", "thanks", "thank you", "yes", "no",
}
MEMORY_MARKERS = (
    "помни", "помнишь", "раньше", "до этого", "мы обсуждали", "мой проект",
    "предыдущ", "решение", "история", "remember", "previous", "before",
    "we discussed", "my project", "decision", "history",
)


def should_recall(query: str, mode: str | RecallMode = RecallMode.AUTO) -> tuple[bool, str]:
    """Return a conservative pre-reply retrieval decision with an explanation.

    AUTO skips only obviously trivial turns. Substantive or ambiguous queries
    still retrieve memory, preserving recall quality over token savings.
    """
    selected = RecallMode(mode)
    text = " ".join(query.strip().lower().split())
    if selected is RecallMode.OFF:
        return False, "recall mode is off"
    if selected is RecallMode.ALWAYS:
        return True, "recall mode is always"
    if not text:
        return False, "empty query"
    if text in TRIVIAL_CHAT:
        return False, "trivial conversational turn"
    if any(marker in text for marker in MEMORY_MARKERS):
        return True, "explicit memory/history signal"
    if len(text) <= 12 and text.rstrip("!?.,") in TRIVIAL_CHAT:
        return False, "short trivial conversational turn"
    return True, "conservative auto recall for substantive query"


@dataclass(frozen=True)
class PromotionSignals:
    relevance: float = 0.0
    frequency: float = 0.0
    query_diversity: float = 0.0
    recency: float = 0.0
    consolidation: float = 0.0
    conceptual_richness: float = 0.0

    def normalized(self) -> "PromotionSignals":
        values = {
            key: min(1.0, max(0.0, float(value)))
            for key, value in asdict(self).items()
        }
        return PromotionSignals(**values)


# Matches the six documented OpenClaw signal weights so the source pattern stays
# traceable. Fractal keeps its own data model and does not copy OpenClaw storage.
PROMOTION_WEIGHTS = {
    "relevance": 0.30,
    "frequency": 0.24,
    "query_diversity": 0.15,
    "recency": 0.15,
    "consolidation": 0.10,
    "conceptual_richness": 0.06,
}
PROMOTION_THRESHOLD = 0.75
MIN_RECALL_COUNT = 3
MIN_UNIQUE_QUERIES = 3


def explain_promotion(
    signals: PromotionSignals,
    *,
    origin_class: str | OriginClass = OriginClass.OWNER,
    recall_count: int = 0,
    unique_queries: int = 0,
) -> dict:
    """Return an auditable, side-effect-free promotion decision.

    `untrusted` and `system` are structurally ineligible. Frequency can never
    promote them into durable memory. Eligible candidates must pass score,
    recall-count, and query-diversity gates together.
    """
    origin = OriginClass(origin_class)
    normalized = signals.normalized()
    values = asdict(normalized)
    contributions = {
        key: round(values[key] * weight, 6)
        for key, weight in PROMOTION_WEIGHTS.items()
    }
    score = round(sum(contributions.values()), 6)

    blockers: list[str] = []
    if origin in {OriginClass.UNTRUSTED, OriginClass.SYSTEM}:
        blockers.append(f"origin_class={origin.value} is structurally ineligible")
    if recall_count < MIN_RECALL_COUNT:
        blockers.append(f"recall_count<{MIN_RECALL_COUNT}")
    if unique_queries < MIN_UNIQUE_QUERIES:
        blockers.append(f"unique_queries<{MIN_UNIQUE_QUERIES}")
    if score < PROMOTION_THRESHOLD:
        blockers.append(f"score<{PROMOTION_THRESHOLD}")

    decision = "PROMOTE_CANDIDATE" if not blockers else "KEEP_EPISODIC"
    return {
        "decision": decision,
        "score": score,
        "origin_class": origin.value,
        "recall_count": int(recall_count),
        "unique_queries": int(unique_queries),
        "thresholds": {
            "promote": PROMOTION_THRESHOLD,
            "min_recall_count": MIN_RECALL_COUNT,
            "min_unique_queries": MIN_UNIQUE_QUERIES,
        },
        "signals": values,
        "weights": PROMOTION_WEIGHTS,
        "contributions": contributions,
        "blockers": blockers,
        "writes_performed": False,
    }


def plan_consolidation(candidates: Iterable[dict]) -> dict:
    """Build a three-stage consolidation plan without mutating memory.

    collect: preserve source identity/origin
    patterns: group recurring themes at a higher layer
    promotion: evaluate candidates through explicit deterministic gates
    """
    items = list(candidates)
    evaluated = []
    for item in items:
        raw = item.get("signals", {})
        signals = PromotionSignals(**{
            field: raw.get(field, 0.0)
            for field in PROMOTION_WEIGHTS
        })
        evaluated.append({
            "uuid": item.get("uuid"),
            "explanation": explain_promotion(
                signals,
                origin_class=item.get("origin_class", OriginClass.UNTRUSTED.value),
                recall_count=int(item.get("recall_count", 0)),
                unique_queries=int(item.get("unique_queries", 0)),
            ),
        })
    return {
        "mode": "DRY_RUN",
        "stages": ["collect", "patterns", "promotion"],
        "candidate_count": len(items),
        "candidates": evaluated,
        "writes_performed": False,
    }
