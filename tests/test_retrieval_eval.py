from core.graphrag_policy import RetrievalMode
from eval.retrieval_eval import CASES, evaluate_case, evaluate_suite


def test_auto_routes_all_benchmark_cases_correctly():
    report = evaluate_suite()
    assert report["case_count"] >= 6
    assert report["routing_accuracy"] == 1.0
    assert report["mean_shape_score"] == 1.0
    assert report["claims"]["measures_answer_quality"] is False
    assert report["claims"]["measures_truth"] is False


def test_local_shape_excludes_communities():
    case = next(case for case in CASES if case.expected_mode is RetrievalMode.LOCAL)
    row = evaluate_case(case, "local")
    assert row["present_collections"] == ["edges", "entities", "episodes"]
    assert row["authoritative"] is False
    assert row["writes_performed"] is False


def test_global_shape_is_community_only():
    case = next(case for case in CASES if case.expected_mode is RetrievalMode.GLOBAL)
    row = evaluate_case(case, "global")
    assert row["present_collections"] == ["communities"]


def test_drift_shape_combines_local_and_community_context():
    case = next(case for case in CASES if case.expected_mode is RetrievalMode.DRIFT)
    row = evaluate_case(case, "drift")
    assert row["present_collections"] == ["communities", "edges", "entities", "episodes"]


def test_policy_benchmark_is_not_provider_or_truth_evaluation():
    report = evaluate_suite()
    assert report["claims"] == {
        "measures_answer_quality": False,
        "measures_truth": False,
        "measures_live_db_latency": False,
        "measures_policy_routing": True,
        "measures_context_shape": True,
    }
