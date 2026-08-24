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

    AUTO intentionally skips only obviously trivial turns. All substantive or
    ambiguous queries still retrieve memory, preserving recall over token savings.
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


# Inspired by OpenClaw's documented multi-signal promotion approach. The exact
# values are local Fractal policy, not claimed to be a universal optimum.
PROMOTION_WEIGHTS = {
    "relevance": 0.30,
    "frequency": 0.24,
    "query_diversity": 0.15,
    "recency": 0.15,
    "consolidation": 0.10,
    "conceptual_richness": 0.06,
}
PROMOTION_THRESHOLD = 0.65
REVIEW_THRESHOLD = 0.45


def explain_promotion(signals: PromotionSignals) -> dict:
    """Score one candidate and return an auditable, side-effect-free decision."""
    normalized = signals.normalized()
    values = asdict(normalized)
    contributions = {
        key: round(values[key] * weight, 6)
        for key, weight in PROMOTION_WEIGHTS.items()
    }
    score = round(sum(contributions.values()), 6)
    if score >= PROMOTION_THRESHOLD:
        decision = "PROMOTE_CANDIDATE"
    elif score >= REVIEW_THRESHOLD:
        decision = "NEEDS_REVIEW"
    else:
        decision = "KEEP_EPISODIC"
    return {
        "decision": decision,
        "score": score,
        "thresholds": {
            "review": REVIEW_THRESHOLD,
            "promote": PROMOTION_THRESHOLD,
        },
        "signals": values,
        "weights": PROMOTION_WEIGHTS,
        "contributions": contributions,
        "writes_performed": False,
    }


def plan_consolidation(candidates: Iterable[dict]) -> dict:
    """Build a three-stage consolidation plan without mutating memory.

    collect: normalize/retain source identity
    patterns: group recurring themes/subjects at a higher layer
    promotion: evaluate candidates through the explicit gate
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
            "explanation": explain_promotion(signals),
        })
    return {
        "mode": "DRY_RUN",
        "stages": ["collect", "patterns", "promotion"],
        "candidate_count": len(items),
        "candidates": evaluated,
        "writes_performed": False,
    }
