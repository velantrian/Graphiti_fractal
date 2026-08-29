from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


Status = Literal["success", "failure", "partial", "timeout", "aborted"]
ProvenanceState = Literal["unknown", "partial", "complete"]

_REDACTED = "[REDACTED]"
_SENSITIVE_STACK_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "password",
    "passwd",
    "secret",
    "token",
}
_STACK_BEARER_RE = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+)([\"']?)([^\s\"']+)(\2)"
)
_STACK_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(OPENAI_API_KEY|API_KEY|ACCESS_TOKEN|REFRESH_TOKEN|PASSWORD|PASSWD|SECRET|TOKEN)"
    r"(\s*=\s*)([\"']?)([^\s\"']+)(\3)"
)


def _redact_stack_value(value: Any, *, key: str | None = None) -> Any:
    """Redact bounded secret forms from caller-supplied environment metadata."""
    normalized_key = (key or "").strip().lower().replace("-", "_")
    if normalized_key in _SENSITIVE_STACK_KEYS:
        return _REDACTED
    if isinstance(value, dict):
        return {
            str(item_key): _redact_stack_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_stack_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_stack_value(item) for item in value]
    if isinstance(value, str):
        redacted = _STACK_BEARER_RE.sub(
            lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}{match.group(4)}",
            value,
        )
        return _STACK_ASSIGNMENT_RE.sub(
            lambda match: (
                f"{match.group(1)}{match.group(2)}{match.group(3)}"
                f"{_REDACTED}{match.group(5)}"
            ),
            redacted,
        )
    return value


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
    # Task/run identity
    run_id: str | None = Field(default=None, description="uuid (если есть). Если нет — создадим новый.")
    task_type: str = Field(default="generic")
    goal: str | None = None

    # Context
    project: str | None = None
    repo: str | None = None
    branch: str | None = None
    commit: str | None = None
    stack: dict[str, Any] | None = None  # python/node версии, фреймворк и т.п.
    affected_files: list[str] = Field(default_factory=list)

    @field_validator("stack", mode="before")
    @classmethod
    def redact_stack_secrets(cls, value):
        # Redact before compute_context_hash(), stack_json and stack_kv ever see it.
        return _redact_stack_value(value)

    # Timeline + result
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: Status = "success"
    error_type: str | None = None
    quality_score: float | None = None
    duration_ms: int | None = None

    # Details
    tool_calls: list[ToolCallEvent] = Field(default_factory=list)
    test_runs: list[TestRunEvent] = Field(default_factory=list)
    errors: list[ErrorEvent] = Field(default_factory=list)

    # Optional additive provenance contract. Omission preserves legacy callers.
    provenance: ExperienceProvenance | None = None


class ExperienceQuery(BaseModel):
    task_type: str | None = None
    context_hash: str | None = None
    limit: int = 5


class ExperienceResult(BaseModel):
    items: list[dict[str, Any]]
