import pytest

from core.derived_graph import (
    CausalHypothesis,
    ConfidenceProfile,
    build_evidence_topology,
    build_provenance_lineage,
    explain_causal_hypothesis,
    plan_gds_analysis,
)


def test_causal_hypothesis_is_non_authoritative_and_side_effect_free():
    result = explain_causal_hypothesis(
        CausalHypothesis(
            cause_id="event:a",
            effect_id="event:b",
            method="manual-domain-hypothesis",
            confidence=ConfidenceProfile(
                source_quality=0.9,
                evidence_count=0.8,
                independence=0.7,
                recency=0.9,
                causal_strength=0.6,
                contradiction_penalty=0.2,
            ),
            supporting_episode_ids=("ep:1", "ep:2"),
            contradicting_episode_ids=("ep:3",),
            confounders=("seasonality",),
        )
    )
    assert result["kind"] == "CAUSAL_HYPOTHESIS"
    assert result["authoritative_fact"] is False
    assert result["writes_performed"] is False
    assert result["supported_by"] == ["ep:1", "ep:2"]
    assert result["contradicted_by"] == ["ep:3"]


def test_causal_hypothesis_rejects_self_causation_and_missing_method():
    profile = ConfidenceProfile()
    with pytest.raises(ValueError):
        explain_causal_hypothesis(
            CausalHypothesis("x", "x", "manual", profile)
        )
    with pytest.raises(ValueError):
        explain_causal_hypothesis(
            CausalHypothesis("x", "y", "", profile)
        )


def test_provenance_requires_sources_and_performs_no_write():
    result = build_provenance_lineage(
        derived_id="claim:7",
        source_episode_ids=["episode:1", "episode:2"],
        activity="bounded-synthesis",
        agent="fractal",
    )
    assert result["wasDerivedFrom"] == ["episode:1", "episode:2"]
    assert result["writes_performed"] is False
    with pytest.raises(ValueError):
        build_provenance_lineage(
            derived_id="claim:8",
            source_episode_ids=[],
            activity="bounded-synthesis",
            agent="fractal",
        )


def test_evidence_topology_is_explicit_and_non_authoritative():
    result = build_evidence_topology(
        claim_id="claim:1",
        relations=[("SUPPORTS", "ep:1"), ("CONTRADICTS", "ep:2")],
    )
    assert result["authoritative_fact"] is False
    assert result["writes_performed"] is False
    assert result["edges"][1]["relation"] == "CONTRADICTS"


def test_gds_policy_allows_stream_stats_and_blocks_mutation():
    stream = plan_gds_analysis(algorithm="louvain", mode="stream")
    assert stream["writes_performed"] is False
    assert stream["results_authoritative"] is False
    stats = plan_gds_analysis(algorithm="leiden", mode="stats")
    assert stats["mode"] == "stats"
    for mode in ("mutate", "write"):
        with pytest.raises(ValueError):
            plan_gds_analysis(algorithm="louvain", mode=mode)
