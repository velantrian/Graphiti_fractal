import asyncio
import json
from types import SimpleNamespace

from experience.models import (
    ExperienceIngestRequest,
    ExperienceProvenance,
    ToolCallEvent,
    ToolCallProvenance,
)
from experience.writer import (
    build_experience_provenance_envelope,
    compute_context_hash,
    ingest_experience,
    provenance_envelope_digest,
)


def test_context_hash_is_independent_from_provenance_metadata():
    legacy = ExperienceIngestRequest(
        task_type="fix_bug",
        project="proj",
        repo="repo",
        stack={"python": "3.12"},
    )
    enriched = ExperienceIngestRequest(
        task_type="fix_bug",
        project="proj",
        repo="repo",
        stack={"python": "3.12"},
        provenance=ExperienceProvenance(
            provider="openai",
            model="example-model",
            runtime_id="runner-7",
            environment_id="env-a",
            provenance_state="complete",
        ),
    )

    assert compute_context_hash(legacy) == compute_context_hash(enriched)


def test_envelope_defaults_to_unknown_and_never_claims_authority():
    envelope = build_experience_provenance_envelope(
        ExperienceIngestRequest(task_type="generic")
    )

    assert envelope["version"] == "experience-provenance-v0"
    assert envelope["provenance_state"] == "unknown"
    assert envelope["authoritative"] is False


def test_envelope_digest_is_deterministic_for_equivalent_metadata():
    first = ExperienceIngestRequest(
        task_type="deploy",
        repo="repo",
        branch="main",
        commit="abc123",
        provenance=ExperienceProvenance(
            environment_id="env-a",
            capability_profile_hash="cap-hash",
            provenance_state="partial",
        ),
        tool_calls=[
            ToolCallEvent(
                tool="browser",
                args={"b": 2, "a": 1},
                exit_code=0,
                provenance=ToolCallProvenance(
                    canonical_tool_id="tool.browser",
                    tool_version="1.2.3",
                    tool_schema_digest="schema-sha",
                    capabilities=["write", "read", "read"],
                    permission_scope=["network", "workspace"],
                    provenance_state="complete",
                ),
            )
        ],
    )
    second = ExperienceIngestRequest(
        task_type="deploy",
        repo="repo",
        branch="main",
        commit="abc123",
        provenance=ExperienceProvenance(
            environment_id="env-a",
            capability_profile_hash="cap-hash",
            provenance_state="partial",
        ),
        tool_calls=[
            ToolCallEvent(
                tool="browser",
                args={"a": 1, "b": 2},
                exit_code=0,
                provenance=ToolCallProvenance(
                    canonical_tool_id="tool.browser",
                    tool_version="1.2.3",
                    tool_schema_digest="schema-sha",
                    capabilities=["read", "write"],
                    permission_scope=["workspace", "network"],
                    provenance_state="complete",
                ),
            )
        ],
    )

    assert provenance_envelope_digest(first) == provenance_envelope_digest(second)


def test_envelope_keeps_raw_tool_arguments_out_of_provenance_json():
    secret = "super-secret-token"
    req = ExperienceIngestRequest(
        tool_calls=[ToolCallEvent(tool="api", args={"token": secret})]
    )

    envelope_json = json.dumps(build_experience_provenance_envelope(req), sort_keys=True)

    assert secret not in envelope_json
    assert envelope_json.count("args_sha256") == 1


class _FakeDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return SimpleNamespace(records=[])


class _FakeGraphiti:
    def __init__(self):
        self.driver = _FakeDriver()


def test_ingest_persists_run_and_tool_provenance_as_additive_observations(monkeypatch):
    monkeypatch.setattr(
        "experience.writer.get_config",
        lambda: SimpleNamespace(memory=SimpleNamespace(experience_group_id="experience")),
    )
    req = ExperienceIngestRequest(
        run_id="run-1",
        task_type="fix_bug",
        repo="repo",
        branch="main",
        commit="abc123",
        provenance=ExperienceProvenance(
            provider=" provider-a ",
            model=" model-a ",
            runtime_id=" python-3.12 ",
            os_name=" linux ",
            environment_id=" env-a ",
            capability_profile_hash=" cap-hash ",
            trace_id=" trace-run ",
            parent_span_id=" span-parent ",
            provenance_state="complete",
        ),
        tool_calls=[
            ToolCallEvent(
                tool="browser",
                args={"query": "safe"},
                exit_code=0,
                provenance=ToolCallProvenance(
                    canonical_tool_id=" tool.browser ",
                    tool_version=" 1.2.3 ",
                    tool_schema_digest=" schema-sha ",
                    capabilities=[" read ", "search", "read"],
                    permission_scope=[" network ", "network"],
                    trace_id=" trace-tool ",
                    parent_span_id=" span-run ",
                    provenance_state="complete",
                ),
            )
        ],
    )
    graphiti = _FakeGraphiti()

    result = asyncio.run(ingest_experience(graphiti, req))

    run_call = next(
        kwargs for query, kwargs in graphiti.driver.calls if "MERGE (tr:TaskRun" in query
    )
    tool_call = next(
        kwargs for query, kwargs in graphiti.driver.calls if "MERGE (t:ToolCall" in query
    )

    assert run_call["provenance_version"] == "experience-provenance-v0"
    assert run_call["provenance_state"] == "complete"
    assert run_call["provenance_provider"] == "provider-a"
    assert run_call["provenance_model"] == "model-a"
    assert run_call["provenance_runtime_id"] == "python-3.12"
    assert run_call["provenance_os_name"] == "linux"
    assert run_call["provenance_environment_id"] == "env-a"
    assert run_call["provenance_capability_profile_hash"] == "cap-hash"
    assert run_call["trace_id"] == "trace-run"
    assert run_call["parent_span_id"] == "span-parent"
    assert run_call["provenance_digest"]

    assert tool_call["provenance_version"] == "tool-provenance-v0"
    assert tool_call["canonical_tool_id"] == "tool.browser"
    assert tool_call["tool_version"] == "1.2.3"
    assert tool_call["tool_schema_digest"] == "schema-sha"
    assert tool_call["capabilities"] == ["read", "search"]
    assert tool_call["permission_scope"] == ["network"]
    assert tool_call["trace_id"] == "trace-tool"
    assert tool_call["parent_span_id"] == "span-run"
    assert tool_call["args_sha256"]

    assert result["provenance"]["authoritative"] is False
    assert result["provenance"]["state"] == "complete"
