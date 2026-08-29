from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .redaction import redact_value


Status = Literal["success", "failure", "partial", "timeout", "aborted"]
ProvenanceState = Literal["unknown", "partial", "complete"]


class ToolCallProvenance(BaseModel):
    """Optional recorded provenance for one observed tool call.

    These fields describe what was recorded about the historical call. They do
    not grant trust, permission, applicability, or execution authority.
    """

    version: Literal["tool-provenance-v0"] = "tool-provenance-v0"
    canonical_tool_id: str | None = None
    tool_version: str | None = None
    tool_schema_digest: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    permission_scope: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    parent_span_id: str | None = None
    provenance_state: ProvenanceState = "unknown"


class ExperienceProvenance(BaseModel):
    """Optional run-level provenance envelope metadata.

    `provenance_state` is caller-supplied evidence about recording completeness;
    it is never inferred as trust or authority by the ingest path.
    """

    version: Literal["experience-provenance-v0"] = "experience-provenance-v0"
    provider: str | None = None
    model: str | None = None
    runtime_id: str | None = None
    os_name: str | None = None
    environment_id: str | None = None
    capability_profile_hash: str | None = None
    trace_id: str | None = None
    parent_span_id: str | None = None
    provenance_state: ProvenanceState = "unknown"


class ToolCallEvent(BaseModel):
    tool: str
    command: str | None = None
    args: dict[str, Any] | None = None
    exit_code: int | None = None
    duration_ms: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    provenance: ToolCallProvenance | None = None


class TestRunEvent(BaseModel):
    framework: str | None = None
    command: str
    passed: bool | None = None
    duration_ms: int | None = None
    summary: str | None = None


class ErrorEvent(BaseModel):
    error_type: str
    message: str | None = None
    stack: str | None = None
    file: str | None = None
    line: int | None = None


class ExperienceIngestRequest(BaseModel):
    run_id: str | None = Field(default=None, description="uuid (если есть). Если нет — создадим новый.")
    task_type: str = Field(default="generic")
    goal: str | None = None

    project: str | None = None
    repo: str | None = None
    branch: str | None = None
    commit: str | None = None
    stack: dict[str, Any] | None = None
    affected_files: list[str] = Field(default_factory=list)

    @field_validator("stack", mode="before")
    @classmethod
    def redact_stack_secrets(cls, value):
        return redact_value(value)

    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: Status = "success"
    error_type: str | None = None
    quality_score: float | None = None
    duration_ms: int | None = None

    tool_calls: list[ToolCallEvent] = Field(default_factory=list)
    test_runs: list[TestRunEvent] = Field(default_factory=list)
    errors: list[ErrorEvent] = Field(default_factory=list)
    provenance: ExperienceProvenance | None = None


class ExperienceQuery(BaseModel):
    task_type: str | None = None
    context_hash: str | None = None
    limit: int = 5


class ExperienceResult(BaseModel):
    items: list[dict[str, Any]]
