from core.graphrag_policy import RetrievalMode, apply_mode_weights, plan_retrieval
from core.types import SearchResult


def test_auto_local_is_default_and_read_only():
    plan = plan_retrieval("What did Alice say yesterday?")
    assert plan.effective_mode is RetrievalMode.LOCAL
    assert plan.use_communities is False
    assert plan.authoritative is False
    assert plan.writes_performed is False


def test_auto_global_for_corpus_level_question():
    plan = plan_retrieval("What are the main themes across the whole corpus?")
    assert plan.effective_mode is RetrievalMode.GLOBAL
    assert plan.use_communities is True
    assert plan.use_entities is False


def test_auto_drift_combines_local_and_community_context():
    plan = plan_retrieval("Why are these projects connected and how do they influence each other?")
    assert plan.effective_mode is RetrievalMode.DRIFT
    assert plan.use_entities is True
    assert plan.use_edges is True
    assert plan.use_communities is True
    assert plan.local_weight > plan.community_weight


def test_global_filters_local_results_without_writes():
    result = SearchResult(
        episodes=[{"uuid": "e1", "score": 0.9}],
        entities=[{"uuid": "n1", "score": 0.8}],
        edges=[{"uuid": "r1", "score": 0.7}],
        communities=[{"uuid": "c1", "score": 0.6}],
        total_episodes=1,
        total_entities=1,
        total_edges=1,
        total_communities=1,
    )
    plan = plan_retrieval("overall themes", "global")
    apply_mode_weights(result, plan)
    assert result.episodes == []
    assert result.entities == []
    assert result.edges == []
    assert result.communities[0]["uuid"] == "c1"
    assert result.total_communities == 1


def test_invalid_mode_fails_closed():
    try:
        plan_retrieval("hello", "magic")
    except ValueError as exc:
        assert "auto|local|global|drift" in str(exc)
    else:
        raise AssertionError("invalid retrieval mode must fail")
