from core.dedupe_review import episode_candidate_report, entity_candidate_report


def test_same_summary_different_bodies_is_not_duplicate():
    episodes = [
        {"uuid":"a", "group_id":"project", "text":"Decision is pending", "summary":"Crystal meeting"},
        {"uuid":"b", "group_id":"project", "text":"Decision was approved", "summary":"Crystal meeting"},
    ]
    assert episode_candidate_report(episodes)["candidate_groups"] == []


def test_same_body_different_namespace_is_not_duplicate():
    episodes = [
        {"uuid":"a", "group_id":"project", "text":"Hello"},
        {"uuid":"b", "group_id":"personal", "text":"hello"},
    ]
    assert episode_candidate_report(episodes)["candidate_groups"] == []


def test_identical_bodies_are_only_review_candidates():
    result = episode_candidate_report([
        {"uuid":"a", "group_id":"project", "text":"hello"},
        {"uuid":"b", "group_id":"project", "text":"Hello"},
    ])
    assert result["writes_performed"] is False
    assert result["candidate_groups"][0]["authorized_to_delete"] is False


def test_entity_equal_name_does_not_authorize_merge():
    result = entity_candidate_report([
        {"uuid":"a", "group_id":"project", "normalized_name":"graphiti"},
        {"uuid":"b", "group_id":"project", "normalized_name":"graphiti"},
    ])
    assert result["candidate_groups"][0]["authorized_to_merge"] is False
