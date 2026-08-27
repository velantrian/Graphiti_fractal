import asyncio
import json
from types import SimpleNamespace

import pytest

from experience.models import ExperienceIngestRequest, ToolCallEvent
from experience.writer import canonical_tool_args, compute_context_hash, ingest_experience


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


