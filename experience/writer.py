from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from core.config import get_config
from .models import ExperienceIngestRequest


_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
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
_BEARER_RE = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+)([\"']?)([^\s\"']+)(\2)"
)
_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(OPENAI_API_KEY|API_KEY|ACCESS_TOKEN|REFRESH_TOKEN|PASSWORD|PASSWD|SECRET|TOKEN)"
    r"(\s*=\s*)([\"']?)([^\s\"']+)(\3)"
)


EXPERIENCE_PROVENANCE_VERSION = "experience-provenance-v0"
TOOL_PROVENANCE_VERSION = "tool-provenance-v0"


def _norm(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _canonical_string_list(values) -> list[str]:
    return sorted({str(value).strip() for value in values or [] if str(value).strip()})


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_digest(value) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _child_uuid(run_uuid: str, kind: str, index: int) -> str:
    """Return a stable UUID for one bounded child position within a TaskRun."""
    return str(uuid5(NAMESPACE_URL, f"fractal-experience:{run_uuid}:{kind}:{index}"))


def compute_context_hash(req: ExperienceIngestRequest) -> str:
    stack_part = ""
    if req.stack:
        items = sorted((str(key), str(value)) for key, value in req.stack.items())
        stack_part = "|".join(f"{key}={value}" for key, value in items)
    base = "|".join(
        [_norm(req.repo or ""), _norm(req.project or ""), _norm(req.task_type), stack_part]
    )
    return sha256(base.encode("utf-8")).hexdigest()


def _tool_chain(req: ExperienceIngestRequest) -> list[str]:
    return [_norm(call.tool) for call in req.tool_calls if call.tool]


def redact_text(text: str | None) -> str | None:
    """Redact bounded secret-like forms before durable Experience persistence."""
    if text is None:
        return None
    redacted = _BEARER_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}{match.group(4)}",
        text,
    )
    redacted = _ASSIGNMENT_RE.sub(
        lambda match: (
            f"{match.group(1)}{match.group(2)}{match.group(3)}"
            f"{_REDACTED}{match.group(5)}"
        ),
        redacted,
    )
    return redacted


def _redact_value(value: Any, *, key: str | None = None) -> Any:
    normalized_key = (key or "").strip().lower().replace("-", "_")
    if normalized_key in _SENSITIVE_KEYS:
        return _REDACTED
    if isinstance(value, dict):
        return {
            str(item_key): _redact_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def canonical_tool_args(args: dict | None) -> tuple[str | None, str | None]:
    """Encode redacted structured tool arguments as deterministic Neo4j-safe scalar JSON.

    Tool args are an API-shaped JSON structure. Unexpected Python objects must
    fail closed instead of being coerced to implementation-dependent strings
    (unlike `_canonical_json`, this never passes `default=str`). Secret-bearing
    keys/values are redacted before the args are ever serialized or hashed, so
    no digest or persisted JSON can be used to recover a redacted secret.
    """
    if args is None:
        return None, None
    safe_args = _redact_value(args)
    encoded = json.dumps(safe_args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return encoded, sha256(encoded.encode("utf-8")).hexdigest()


def _args_digest(args) -> str | None:
    return canonical_tool_args(args)[1]


def build_experience_provenance_envelope(req: ExperienceIngestRequest) -> dict:
    """Build a deterministic, non-authoritative provenance envelope.

    The envelope records what the caller supplied about a historical run. It does
    not infer trust, correctness, applicability, permissions, or promotion state.
    Raw tool arguments and outputs are deliberately excluded; only an args digest
    is included for lineage comparison.
    """
    run_provenance = req.provenance
    tool_entries: list[dict] = []

    for index, call in enumerate(req.tool_calls):
        tool_provenance = call.provenance
        tool_entries.append(
            {
                "index": index,
                "tool": _clean_optional(call.tool),
                "provenance_version": (
                    tool_provenance.version if tool_provenance else TOOL_PROVENANCE_VERSION
                ),
                "provenance_state": (
                    tool_provenance.provenance_state if tool_provenance else "unknown"
                ),
                "canonical_tool_id": (
                    _clean_optional(tool_provenance.canonical_tool_id)
                    if tool_provenance
                    else None
                ),
                "tool_version": (
                    _clean_optional(tool_provenance.tool_version) if tool_provenance else None
                ),
                "tool_schema_digest": (
                    _clean_optional(tool_provenance.tool_schema_digest)
                    if tool_provenance
                    else None
                ),
                "capabilities": _canonical_string_list(
                    tool_provenance.capabilities if tool_provenance else []
                ),
                "permission_scope": _canonical_string_list(
                    tool_provenance.permission_scope if tool_provenance else []
                ),
                "trace_id": (
                    _clean_optional(tool_provenance.trace_id) if tool_provenance else None
                ),
                "parent_span_id": (
                    _clean_optional(tool_provenance.parent_span_id)
                    if tool_provenance
                    else None
                ),
                "args_sha256": _args_digest(call.args),
                "exit_code": call.exit_code,
            }
        )

    return {
        "version": run_provenance.version if run_provenance else EXPERIENCE_PROVENANCE_VERSION,
        "provenance_state": (
            run_provenance.provenance_state if run_provenance else "unknown"
        ),
        "provider": _clean_optional(run_provenance.provider) if run_provenance else None,
        "model": _clean_optional(run_provenance.model) if run_provenance else None,
        "runtime_id": _clean_optional(run_provenance.runtime_id) if run_provenance else None,
        "os_name": _clean_optional(run_provenance.os_name) if run_provenance else None,
        "environment_id": (
            _clean_optional(run_provenance.environment_id) if run_provenance else None
        ),
        "capability_profile_hash": (
            _clean_optional(run_provenance.capability_profile_hash)
            if run_provenance
            else None
        ),
        "trace_id": _clean_optional(run_provenance.trace_id) if run_provenance else None,
        "parent_span_id": (
            _clean_optional(run_provenance.parent_span_id) if run_provenance else None
        ),
        "repo": _clean_optional(req.repo),
        "branch": _clean_optional(req.branch),
        "commit": _clean_optional(req.commit),
        "tool_calls": tool_entries,
        "authoritative": False,
    }


def provenance_envelope_digest(req: ExperienceIngestRequest) -> str:
    return _json_digest(build_experience_provenance_envelope(req))


def _truncate(text: str | None, limit: int = 4000) -> str | None:
    if not text:
        return None
    cleaned = text.strip()
    return cleaned if len(cleaned) <= limit else cleaned[:limit] + "..."


def _safe_text(text: str | None, limit: int) -> str | None:
    return _truncate(redact_text(text), limit)


async def ingest_experience(graphiti, req: ExperienceIngestRequest) -> dict:
    """Persist one structured task-run experience without LLM extraction."""
    run_uuid = req.run_id or str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    started_at = (req.started_at or datetime.now(timezone.utc)).isoformat()
    ended_at = (req.ended_at or datetime.now(timezone.utc)).isoformat()
    group_id = get_config().memory.experience_group_id

    context_hash = compute_context_hash(req)
    tool_chain = _tool_chain(req)
    tool_chain_hash = sha256("|".join(tool_chain).encode("utf-8")).hexdigest() if tool_chain else None
    stack_json = json.dumps(req.stack or {}, ensure_ascii=False, sort_keys=True)
    stack_kv = [
        f"{key}={value}"
        for key, value in sorted((req.stack or {}).items(), key=lambda item: str(item[0]))
    ]

    # Validate/canonicalize every bounded ToolCall's args before any durable
    # write begins. A malformed (non-JSON-serializable) args value must fail
    # closed here — before the TaskRun MERGE — rather than after some parent
    # state (TaskRun/Project/Repo/File rows) has already been persisted.
    canonical_tool_calls = [(call, *canonical_tool_args(call.args)) for call in req.tool_calls]

    provenance_envelope = build_experience_provenance_envelope(req)
    provenance_json = _canonical_json(provenance_envelope)
    provenance_digest = sha256(provenance_json.encode("utf-8")).hexdigest()

    driver = graphiti.driver
    await driver.execute_query(
        """
        MERGE (tr:TaskRun {uuid:$uuid})
        ON CREATE SET tr.created_at=$now
        SET tr.group_id=$gid,
            tr.task_type=$task_type,
            tr.goal=$goal,
            tr.project=$project,
            tr.repo=$repo,
            tr.branch=$branch,
            tr.commit=$commit,
            tr.stack_json=$stack_json,
            tr.stack_kv=$stack_kv,
            tr.affected_files=$affected_files,
            tr.started_at=$started_at,
            tr.ended_at=$ended_at,
            tr.status=$status,
            tr.error_type=$error_type,
            tr.quality_score=$quality_score,
            tr.duration_ms=$duration_ms,
            tr.context_hash=$context_hash,
            tr.tool_chain=$tool_chain,
            tr.tool_chain_hash=$tool_chain_hash,
            tr.provenance_version=$provenance_version,
            tr.provenance_state=$provenance_state,
            tr.provenance_digest=$provenance_digest,
            tr.provenance_json=$provenance_json,
            tr.provenance_provider=$provenance_provider,
            tr.provenance_model=$provenance_model,
            tr.provenance_runtime_id=$provenance_runtime_id,
            tr.provenance_os_name=$provenance_os_name,
            tr.provenance_environment_id=$provenance_environment_id,
            tr.provenance_capability_profile_hash=$provenance_capability_profile_hash,
            tr.trace_id=$trace_id,
            tr.parent_span_id=$parent_span_id
        """,
        uuid=run_uuid,
        now=now,
        gid=group_id,
        task_type=req.task_type,
        goal=req.goal,
        project=req.project,
        repo=req.repo,
        branch=req.branch,
        commit=req.commit,
        stack_json=stack_json,
        stack_kv=stack_kv,
        affected_files=req.affected_files,
        started_at=started_at,
        ended_at=ended_at,
        status=req.status,
        error_type=req.error_type,
        quality_score=req.quality_score,
        duration_ms=req.duration_ms,
        context_hash=context_hash,
        tool_chain=tool_chain,
        tool_chain_hash=tool_chain_hash,
        provenance_version=provenance_envelope["version"],
        provenance_state=provenance_envelope["provenance_state"],
        provenance_digest=provenance_digest,
        provenance_json=provenance_json,
        provenance_provider=provenance_envelope["provider"],
        provenance_model=provenance_envelope["model"],
        provenance_runtime_id=provenance_envelope["runtime_id"],
        provenance_os_name=provenance_envelope["os_name"],
        provenance_environment_id=provenance_envelope["environment_id"],
        provenance_capability_profile_hash=provenance_envelope["capability_profile_hash"],
        trace_id=provenance_envelope["trace_id"],
        parent_span_id=provenance_envelope["parent_span_id"],
    )

    if req.project:
        await driver.execute_query(
            """
            MERGE (p:Project {name:$name})
            ON CREATE SET p.created_at=$now
            SET p.group_id=$gid
            WITH p
            MATCH (tr:TaskRun {uuid:$uuid})
            MERGE (tr)-[:IN_PROJECT]->(p)
            """,
            name=req.project,
            now=now,
            gid=group_id,
            uuid=run_uuid,
        )

    if req.repo:
        await driver.execute_query(
            """
            MERGE (r:Repo {name:$name})
            ON CREATE SET r.created_at=$now
            SET r.group_id=$gid
            WITH r
            MATCH (tr:TaskRun {uuid:$uuid})
            MERGE (tr)-[:IN_REPO]->(r)
            """,
            name=req.repo,
            now=now,
            gid=group_id,
            uuid=run_uuid,
        )

    for path in req.affected_files[:50]:
        await driver.execute_query(
            """
            MERGE (f:File {path:$path})
            ON CREATE SET f.created_at=$now
            SET f.group_id=$gid
            WITH f
            MATCH (tr:TaskRun {uuid:$uuid})
            MERGE (tr)-[:AFFECTED_FILE]->(f)
            """,
            path=path,
            now=now,
            gid=group_id,
            uuid=run_uuid,
        )

    tool_nodes = 0
    for index, (call, args_json, args_sha256) in enumerate(canonical_tool_calls[:100]):
        tool_entry = provenance_envelope["tool_calls"][index]
        await driver.execute_query(
            """
            MERGE (t:ToolCall {uuid:$uuid})
            ON CREATE SET t.created_at=$now
            SET t.group_id=$gid, t.tool=$tool,
                t.command=$command, t.args_json=$args_json, t.exit_code=$exit_code,
                t.duration_ms=$duration_ms, t.stdout=$stdout, t.stderr=$stderr,
                t.provenance_version=$provenance_version,
                t.provenance_state=$provenance_state,
                t.canonical_tool_id=$canonical_tool_id,
                t.tool_version=$tool_version,
                t.tool_schema_digest=$tool_schema_digest,
                t.capabilities=$capabilities,
                t.permission_scope=$permission_scope,
                t.args_sha256=$args_sha256,
                t.trace_id=$trace_id,
                t.parent_span_id=$parent_span_id
            WITH t
            MATCH (tr:TaskRun {uuid:$run_uuid})
            MERGE (tr)-[:HAS_TOOLCALL]->(t)
            """,
            uuid=_child_uuid(run_uuid, "tool", index),
            now=now,
            gid=group_id,
            run_uuid=run_uuid,
            tool=call.tool,
            command=_safe_text(call.command, 4000),
            args_json=args_json,
            exit_code=call.exit_code,
            duration_ms=call.duration_ms,
            stdout=_safe_text(call.stdout, 4000),
            stderr=_safe_text(call.stderr, 4000),
            provenance_version=tool_entry["provenance_version"],
            provenance_state=tool_entry["provenance_state"],
            canonical_tool_id=tool_entry["canonical_tool_id"],
            tool_version=tool_entry["tool_version"],
            tool_schema_digest=tool_entry["tool_schema_digest"],
            capabilities=tool_entry["capabilities"],
            permission_scope=tool_entry["permission_scope"],
            args_sha256=args_sha256,
            trace_id=tool_entry["trace_id"],
            parent_span_id=tool_entry["parent_span_id"],
        )
        tool_nodes += 1

    test_nodes = 0
    for index, test_run in enumerate(req.test_runs[:50]):
        await driver.execute_query(
            """
            MERGE (t:TestRun {uuid:$uuid})
            ON CREATE SET t.created_at=$now
            SET t.group_id=$gid, t.framework=$framework,
                t.command=$command, t.passed=$passed, t.duration_ms=$duration_ms,
                t.summary=$summary
            WITH t
            MATCH (run:TaskRun {uuid:$run_uuid})
            MERGE (run)-[:HAS_TESTRUN]->(t)
            """,
            uuid=_child_uuid(run_uuid, "test", index),
            now=now,
            gid=group_id,
            run_uuid=run_uuid,
            framework=test_run.framework,
            command=_safe_text(test_run.command, 4000),
            passed=test_run.passed,
            duration_ms=test_run.duration_ms,
            summary=_safe_text(test_run.summary, 2000),
        )
        test_nodes += 1

    error_nodes = 0
    for index, error in enumerate(req.errors[:50]):
        await driver.execute_query(
            """
            MERGE (e:ErrorEvent {uuid:$uuid})
            ON CREATE SET e.created_at=$now
            SET e.group_id=$gid, e.error_type=$error_type,
                e.message=$message, e.stack=$stack, e.file=$file, e.line=$line
            WITH e
            MATCH (run:TaskRun {uuid:$run_uuid})
            MERGE (run)-[:FAILED_WITH]->(e)
            """,
            uuid=_child_uuid(run_uuid, "error", index),
            now=now,
            gid=group_id,
            run_uuid=run_uuid,
            error_type=error.error_type,
            message=_safe_text(error.message, 2000),
            stack=_safe_text(error.stack, 8000),
            file=error.file,
            line=error.line,
        )
        error_nodes += 1

    return {
        "status": "ok",
        "run_id": run_uuid,
        "context_hash": context_hash,
        "provenance": {
            "version": provenance_envelope["version"],
            "digest": provenance_digest,
            "state": provenance_envelope["provenance_state"],
            "authoritative": False,
        },
        "created": {
            "tool_calls": tool_nodes,
            "test_runs": test_nodes,
            "errors": error_nodes,
        },
    }
