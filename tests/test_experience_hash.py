import asyncio
import json
from types import SimpleNamespace

import pytest

from experience.models import ErrorEvent, ExperienceIngestRequest, TestRunEvent, ToolCallEvent
from experience.writer import (
    canonical_tool_args,
    compute_context_hash,
    ingest_experience,
    redact_text,
)


def test_compute_context_hash_stable():
    req1 = ExperienceIngestRequest(
        task_type="fix_bug",
        project="proj",
        repo="repo",
        stack={"python": "3.11", "framework": "fastapi"},
    )
    req2 = ExperienceIngestRequest(
        task_type="fix_bug",
        project="proj",
        repo="repo",
        stack={"framework": "fastapi", "python": "3.11"},
    )
    assert compute_context_hash(req1) == compute_context_hash(req2)


def test_canonical_tool_args_is_deterministic_and_scalar_safe():
    first_json, first_digest = canonical_tool_args(
        {"nested": {"b": 2, "a": 1}, "items": ["x", 3], "flag": True}
    )
    second_json, second_digest = canonical_tool_args(
        {"flag": True, "items": ["x", 3], "nested": {"a": 1, "b": 2}}
    )

    assert isinstance(first_json, str)
    assert first_json == second_json
    assert first_digest == second_digest
    assert json.loads(first_json) == {
        "flag": True,
        "items": ["x", 3],
        "nested": {"a": 1, "b": 2},
    }


def test_canonical_tool_args_preserves_none():
    assert canonical_tool_args(None) == (None, None)


@pytest.mark.parametrize("bad_value", [object(), {"nested"}, {1, 2, 3}])
def test_canonical_tool_args_rejects_non_json_values(bad_value):
    with pytest.raises(TypeError):
        canonical_tool_args({"non_json": bad_value})


def test_canonical_tool_args_rejects_custom_object_instance():
    class Opaque:
        pass

    with pytest.raises(TypeError):
        canonical_tool_args({"instance": Opaque()})


class _RejectingFakeDriver:
    """Fails the test if a Neo4j write is attempted at all."""

    async def execute_query(self, query, **kwargs):
        raise AssertionError("execute_query must not be called for invalid ToolCall args")


class _RejectingFakeGraphiti:
    def __init__(self):
        self.driver = _RejectingFakeDriver()


def test_invalid_tool_args_fail_closed_before_any_durable_write(monkeypatch):
    monkeypatch.setattr(
        "experience.writer.get_config",
        lambda: SimpleNamespace(memory=SimpleNamespace(experience_group_id="experience")),
    )
    req = ExperienceIngestRequest(
        run_id="run-invalid-args",
        task_type="fix_bug",
        tool_calls=[ToolCallEvent(tool="shell", args={"bad": {1, 2, 3}})],
    )
    graphiti = _RejectingFakeGraphiti()

    with pytest.raises(TypeError):
        asyncio.run(ingest_experience(graphiti, req))


def test_canonical_tool_args_redacts_sensitive_keys_and_embedded_secrets():
    args_json, digest = canonical_tool_args(
        {
            "password": "hunter2",
            "nested": {"api_key": "sk-test-secret", "safe": "visible"},
            "header": "Authorization: Bearer bearer-secret",
            "quoted_header": 'Authorization: Bearer "quoted-bearer-secret"',
            "env": "OPENAI_API_KEY=env-secret",
            "quoted_env": "API_KEY='quoted-env-secret'",
        }
    )
    decoded = json.loads(args_json)

    assert decoded["password"] == "[REDACTED]"
    assert decoded["nested"]["api_key"] == "[REDACTED]"
    assert decoded["nested"]["safe"] == "visible"
    assert "bearer-secret" not in args_json
    assert "quoted-bearer-secret" not in args_json
    assert "env-secret" not in args_json
    assert "quoted-env-secret" not in args_json
    assert digest and len(digest) == 64


def test_redact_text_preserves_labels_quotes_and_safe_text():
    text = (
        "Authorization: Bearer abc123 "
        "Authorization: Bearer \"quoted-bearer\" "
        "OPENAI_API_KEY=sk-value "
        "API_KEY='quoted-api-key' "
        "password = \"quoted-password\" "
        "safe=value"
    )
    redacted = redact_text(text)

    assert "abc123" not in redacted
    assert "quoted-bearer" not in redacted
    assert "sk-value" not in redacted
    assert "quoted-api-key" not in redacted
    assert "quoted-password" not in redacted
    assert "safe=value" in redacted
    assert redacted.count("[REDACTED]") == 5
    assert 'Bearer "[REDACTED]"' in redacted
    assert "API_KEY='[REDACTED]'" in redacted


def test_redact_text_preserves_none():
    assert redact_text(None) is None


class _RecordingDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return SimpleNamespace(records=[])


class _RecordingGraphiti:
    def __init__(self):
        self.driver = _RecordingDriver()


def test_same_run_id_reuses_deterministic_child_ids_and_merge_queries(monkeypatch):
    monkeypatch.setattr(
        "experience.writer.get_config",
        lambda: SimpleNamespace(memory=SimpleNamespace(experience_group_id="experience")),
    )
    req = ExperienceIngestRequest(
        run_id="run-idempotent-001",
        task_type="fix_bug",
        tool_calls=[ToolCallEvent(tool="shell", command="echo ok", args={"safe": "yes"})],
        test_runs=[TestRunEvent(framework="pytest", command="pytest -q", passed=True)],
        errors=[ErrorEvent(error_type="ExampleError", message="bounded")],
    )
    graphiti = _RecordingGraphiti()

    asyncio.run(ingest_experience(graphiti, req))
    asyncio.run(ingest_experience(graphiti, req))

    child_markers = {
        "ToolCall": "MERGE (t:ToolCall {uuid:$uuid})",
        "TestRun": "MERGE (t:TestRun {uuid:$uuid})",
        "ErrorEvent": "MERGE (e:ErrorEvent {uuid:$uuid})",
    }
    for label, marker in child_markers.items():
        writes = [(query, kwargs) for query, kwargs in graphiti.driver.calls if marker in query]
        assert len(writes) == 2, label
        assert writes[0][1]["uuid"] == writes[1][1]["uuid"], label
        assert writes[0][1]["run_uuid"] == "run-idempotent-001"
        assert f"CREATE (t:{label}" not in writes[0][0]
        assert f"CREATE (e:{label}" not in writes[0][0]
