import asyncio

from experience.retrieval import (
    SUCCESS_PATTERN_PAGE_SIZE,
    assess_pattern_applicability,
    filter_success_patterns_for_environment,
    get_success_patterns,
)


def test_legacy_behavior_is_preserved_without_environment_constraints():
    patterns = [{"run_id": "run-1", "tools": ["browser"]}]
    assert filter_success_patterns_for_environment(patterns) == patterns


def test_missing_required_tool_fails_closed_when_environment_is_supplied():
    patterns = [{"run_id": "run-1", "tools": ["browser", "shell"]}]
    result = filter_success_patterns_for_environment(patterns, available_tools={"shell"})
    assert result == []


def test_forbidden_tool_fails_closed_even_if_available():
    patterns = [{"run_id": "run-1", "tools": ["browser"]}]
    result = filter_success_patterns_for_environment(
        patterns,
        available_tools={"browser"},
        forbidden_tools={"browser"},
    )
    assert result == []


def test_empty_available_tools_rejects_any_recorded_requirement():
    patterns = [{"run_id": "run-1", "tools": ["browser"]}]
    assert filter_success_patterns_for_environment(patterns, available_tools=[]) == []


def test_unknown_tool_provenance_fails_closed_when_constrained():
    result = assess_pattern_applicability(
        {"run_id": "run-unknown", "tools": [], "tool_chain": []},
        available_tools={"browser", "shell"},
    )
    assert result["applicable"] is False
    assert result["tool_requirement_known"] is False
    assert result["reason"] == "tool requirements unknown"


def test_unknown_tool_provenance_preserves_legacy_unconstrained_behavior():
    patterns = [{"run_id": "run-unknown", "tools": [], "tool_chain": []}]
    assert filter_success_patterns_for_environment(patterns) == patterns


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
    assert receipt["tool_requirement_known"] is True
    assert receipt["required_tools"] == ["browser"]
    assert receipt["missing_tools"] == []
    assert receipt["forbidden_tools"] == []
    assert receipt["reason"] == "recorded tool requirements compatible with supplied constraints"


def test_tool_chain_is_used_when_tools_collection_is_absent():
    result = assess_pattern_applicability(
        {"run_id": "run-1", "tool_chain": ["GitHub", "pytest"]},
        available_tools={"github", "pytest"},
    )
    assert result["applicable"] is True
    assert result["required_tools"] == ["github", "pytest"]


def test_tools_and_tool_chain_are_unioned_for_guard_decision():
    result = assess_pattern_applicability(
        {
            "run_id": "run-1",
            "tools": [f"tool-{i}" for i in range(10)],
            "tool_chain": [f"tool-{i}" for i in range(11)],
        },
        available_tools=[f"tool-{i}" for i in range(11)],
        forbidden_tools=["tool-10"],
    )
    assert result["applicable"] is False
    assert "tool-10" in result["required_tools"]
    assert result["forbidden_tools"] == ["tool-10"]


def test_normalization_deduplicates_case_and_whitespace():
    result = assess_pattern_applicability(
        {"run_id": "run-1", "tools": [" Browser ", "SEARCH", "browser"]},
        available_tools=["browser", " search "],
    )
    assert result["applicable"] is True
    assert result["required_tools"] == ["browser", "search"]


def test_scalar_legacy_tool_value_is_treated_as_one_name_not_characters():
    result = assess_pattern_applicability(
        {"run_id": "run-1", "tools": "browser"},
        available_tools=["browser"],
    )
    assert result["applicable"] is True
    assert result["required_tools"] == ["browser"]


def test_applicability_does_not_claim_validity_or_authority():
    result = assess_pattern_applicability(
        {"run_id": "run-1", "tools": ["browser"]},
        available_tools={"browser"},
    )
    assert result["applicable"] is True
    assert "validated_lesson" not in result
    assert "authoritative" not in result


class _FakeRecord(dict):
    pass


class _FakeResult:
    def __init__(self, records):
        self.records = records


class _FakeDriver:
    def __init__(self, records):
        self.records = records
        self.calls = []

    async def execute_query(self, _query, **kwargs):
        offset = kwargs.get("offset", 0)
        limit = kwargs["limit"]
        self.calls.append({"offset": offset, "limit": limit})
        return _FakeResult(self.records[offset : offset + limit])


class _FakeGraphiti:
    def __init__(self, records):
        self.driver = _FakeDriver(records)


def test_constrained_retrieval_paginates_until_applicable_row_found(monkeypatch):
    monkeypatch.setattr("experience.retrieval._experience_group_id", lambda: "experience")
    records = [
        _FakeRecord(run_id=f"bad-{i}", tools=["shell"], tool_chain=["shell"])
        for i in range(SUCCESS_PATTERN_PAGE_SIZE + 1)
    ] + [
        _FakeRecord(run_id="good-52", tools=["browser"], tool_chain=["browser"])
    ]
    graphiti = _FakeGraphiti(records)

    result = asyncio.run(
        get_success_patterns(
            graphiti,
            task_type=None,
            context_hash=None,
            limit=1,
            available_tools=["browser"],
        )
    )

    assert [row["run_id"] for row in result] == ["good-52"]
    assert graphiti.driver.calls == [
        {"offset": 0, "limit": SUCCESS_PATTERN_PAGE_SIZE},
        {"offset": SUCCESS_PATTERN_PAGE_SIZE, "limit": SUCCESS_PATTERN_PAGE_SIZE},
    ]


def test_unconstrained_retrieval_keeps_single_bounded_page(monkeypatch):
    monkeypatch.setattr("experience.retrieval._experience_group_id", lambda: "experience")
    records = [
        _FakeRecord(run_id=f"run-{i}", tools=["browser"], tool_chain=["browser"])
        for i in range(10)
    ]
    graphiti = _FakeGraphiti(records)

    result = asyncio.run(
        get_success_patterns(
            graphiti,
            task_type=None,
            context_hash=None,
            limit=3,
        )
    )

    assert [row["run_id"] for row in result] == ["run-0", "run-1", "run-2"]
    assert graphiti.driver.calls == [{"offset": 0, "limit": 3}]
