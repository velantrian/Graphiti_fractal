from experience.retrieval import (
    assess_pattern_applicability,
    filter_success_patterns_for_environment,
)


def test_legacy_behavior_is_preserved_without_environment_constraints():
    patterns = [{"run_id": "run-1", "tools": ["browser"]}]
    assert filter_success_patterns_for_environment(patterns) == patterns


def test_missing_required_tool_fails_closed_when_environment_is_supplied():
    patterns = [{"run_id": "run-1", "tools": ["browser", "shell"]}]
    result = filter_success_patterns_for_environment(
        patterns,
        available_tools={"shell"},
    )
    assert result == []


def test_forbidden_tool_fails_closed_even_if_available():
    patterns = [{"run_id": "run-1", "tools": ["browser"]}]
    result = filter_success_patterns_for_environment(
        patterns,
        available_tools={"browser"},
        forbidden_tools={"browser"},
    )
    assert result == []


def test_applicable_pattern_returns_explicit_receipt():
    patterns = [{"run_id": "run-1", "tools": ["browser"]}]
    result = filter_success_patterns_for_environment(
        patterns,
        available_tools={"browser", "shell"},
    )
    assert len(result) == 1
    receipt = result[0]["applicability"]
    assert receipt["applicable"] is True
    assert receipt["constrained"] is True
    assert receipt["required_tools"] == ["browser"]
    assert receipt["missing_tools"] == []
    assert receipt["forbidden_tools"] == []


def test_tool_chain_is_used_when_tools_collection_is_absent():
    result = assess_pattern_applicability(
        {"run_id": "run-1", "tool_chain": ["GitHub", "pytest"]},
        available_tools={"github", "pytest"},
    )
    assert result["applicable"] is True
    assert result["required_tools"] == ["github", "pytest"]


def test_applicability_does_not_claim_validity_or_authority():
    result = assess_pattern_applicability(
        {"run_id": "run-1", "tools": ["browser"]},
        available_tools={"browser"},
    )
    assert result["applicable"] is True
    assert "validated_lesson" not in result
    assert "authoritative" not in result
