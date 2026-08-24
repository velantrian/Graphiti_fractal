from core.memory_lifecycle import (
    PromotionSignals,
    explain_promotion,
    plan_consolidation,
    should_recall,
)


def test_auto_recall_skips_only_trivial_turns():
    assert should_recall("спасибо", "auto") == (False, "trivial conversational turn")
    enabled, reason = should_recall("Что мы решили по архитектуре проекта?", "auto")
    assert enabled is True
    assert "memory" in reason or "substantive" in reason
    assert should_recall("anything", "off")[0] is False
    assert should_recall("anything", "always")[0] is True


def test_promotion_requires_all_deterministic_gates():
    strong = PromotionSignals(
        relevance=1.0,
        frequency=1.0,
        query_diversity=1.0,
        recency=1.0,
        consolidation=1.0,
        conceptual_richness=1.0,
    )
    result = explain_promotion(
        strong,
        origin_class="owner",
        recall_count=3,
        unique_queries=3,
    )
    assert result["decision"] == "PROMOTE_CANDIDATE"
    assert result["score"] == 1.0
    assert result["blockers"] == []
    assert result["writes_performed"] is False


def test_untrusted_content_can_never_promote_by_score():
    strong = PromotionSignals(
        relevance=1.0,
        frequency=1.0,
        query_diversity=1.0,
        recency=1.0,
        consolidation=1.0,
        conceptual_richness=1.0,
    )
    result = explain_promotion(
        strong,
        origin_class="untrusted",
        recall_count=999,
        unique_queries=999,
    )
    assert result["decision"] == "KEEP_EPISODIC"
    assert any("structurally ineligible" in blocker for blocker in result["blockers"])


def test_consolidation_plan_is_dry_run_and_defaults_unknown_origin_untrusted():
    plan = plan_consolidation([
        {
            "uuid": "candidate-1",
            "signals": {
                "relevance": 1,
                "frequency": 1,
                "query_diversity": 1,
                "recency": 1,
                "consolidation": 1,
                "conceptual_richness": 1,
            },
            "recall_count": 10,
            "unique_queries": 10,
        }
    ])
    assert plan["mode"] == "DRY_RUN"
    assert plan["writes_performed"] is False
    explanation = plan["candidates"][0]["explanation"]
    assert explanation["origin_class"] == "untrusted"
    assert explanation["decision"] == "KEEP_EPISODIC"
