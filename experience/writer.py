from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

from core.config import get_config
from .models import ExperienceIngestRequest


def _norm(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


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


def _truncate(text: str | None, limit: int = 4000) -> str | None:
    if not text:
        return None
    cleaned = text.strip()
    return cleaned if len(cleaned) <= limit else cleaned[:limit] + "..."


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
            tr.tool_chain_hash=$tool_chain_hash
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
    for call in req.tool_calls[:100]:
        await driver.execute_query(
            """
            CREATE (t:ToolCall {
              uuid:$uuid, created_at:$now, group_id:$gid, tool:$tool,
              command:$command, args:$args, exit_code:$exit_code,
              duration_ms:$duration_ms, stdout:$stdout, stderr:$stderr
            })
            WITH t
            MATCH (tr:TaskRun {uuid:$run_uuid})
            MERGE (tr)-[:HAS_TOOLCALL]->(t)
            """,
            uuid=str(uuid4()),
            now=now,
            gid=group_id,
            run_uuid=run_uuid,
            tool=call.tool,
            command=call.command,
            args=call.args,
            exit_code=call.exit_code,
            duration_ms=call.duration_ms,
            stdout=_truncate(call.stdout, 4000),
            stderr=_truncate(call.stderr, 4000),
        )
        tool_nodes += 1

    test_nodes = 0
    for test_run in req.test_runs[:50]:
        await driver.execute_query(
            """
            CREATE (t:TestRun {
              uuid:$uuid, created_at:$now, group_id:$gid, framework:$framework,
              command:$command, passed:$passed, duration_ms:$duration_ms, summary:$summary
            })
            WITH t
            MATCH (run:TaskRun {uuid:$run_uuid})
            MERGE (run)-[:HAS_TESTRUN]->(t)
            """,
            uuid=str(uuid4()),
            now=now,
            gid=group_id,
            run_uuid=run_uuid,
            framework=test_run.framework,
            command=test_run.command,
            passed=test_run.passed,
            duration_ms=test_run.duration_ms,
            summary=_truncate(test_run.summary, 2000),
        )
        test_nodes += 1

    error_nodes = 0
    for error in req.errors[:50]:
        await driver.execute_query(
            """
            CREATE (e:ErrorEvent {
              uuid:$uuid, created_at:$now, group_id:$gid, error_type:$error_type,
              message:$message, stack:$stack, file:$file, line:$line
            })
            WITH e
            MATCH (run:TaskRun {uuid:$run_uuid})
            MERGE (run)-[:FAILED_WITH]->(e)
            """,
            uuid=str(uuid4()),
            now=now,
            gid=group_id,
            run_uuid=run_uuid,
            error_type=error.error_type,
            message=_truncate(error.message, 2000),
            stack=_truncate(error.stack, 8000),
            file=error.file,
            line=error.line,
        )
        error_nodes += 1

    return {
        "status": "ok",
        "run_id": run_uuid,
        "context_hash": context_hash,
        "created": {
            "tool_calls": tool_nodes,
            "test_runs": test_nodes,
            "errors": error_nodes,
        },
    }
