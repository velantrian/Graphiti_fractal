from __future__ import annotations

from collections.abc import Iterable

from core.config import get_config


def _experience_group_id() -> str:
    return get_config().memory.experience_group_id


def _normalized_names(values: Iterable[str] | None) -> set[str] | None:
    if values is None:
        return None
    return {str(value).strip().lower() for value in values if str(value).strip()}


def assess_pattern_applicability(
    pattern: dict,
    *,
    available_tools: Iterable[str] | None = None,
    forbidden_tools: Iterable[str] | None = None,
) -> dict:
    """Return a deterministic environment-applicability assessment.

    This is deliberately narrow: it only reasons about tools recorded on the
    prior TaskRun. It does not claim semantic, causal, or epistemic validity.
    """
    required = _normalized_names(pattern.get("tools") or pattern.get("tool_chain") or []) or set()
    available = _normalized_names(available_tools)
    forbidden = _normalized_names(forbidden_tools) or set()

    missing = sorted(required - available) if available is not None else []
    blocked = sorted(required & forbidden)
    constrained = available is not None or forbidden_tools is not None
    applicable = not missing and not blocked

    return {
        "applicable": applicable,
        "constrained": constrained,
        "required_tools": sorted(required),
        "missing_tools": missing,
        "forbidden_tools": blocked,
        "reason": (
            "tool requirements satisfied"
            if applicable and constrained
            else "no environment constraint supplied"
            if applicable
            else "environment mismatch"
        ),
    }


def filter_success_patterns_for_environment(
    patterns: Iterable[dict],
    *,
    available_tools: Iterable[str] | None = None,
    forbidden_tools: Iterable[str] | None = None,
) -> list[dict]:
    """Filter prior success observations before reuse in a constrained environment.

    When no environment constraint is supplied, legacy behavior is preserved.
    When a constraint is supplied, inapplicable patterns fail closed and every
    returned row carries an explicit applicability receipt.
    """
    constrained = available_tools is not None or forbidden_tools is not None
    rows: list[dict] = []
    for raw in patterns:
        row = dict(raw)
        assessment = assess_pattern_applicability(
            row,
            available_tools=available_tools,
            forbidden_tools=forbidden_tools,
        )
        if constrained and not assessment["applicable"]:
            continue
        if constrained:
            row["applicability"] = assessment
        rows.append(row)
    return rows


async def get_success_patterns(
    graphiti,
    *,
    task_type: str | None,
    context_hash: str | None,
    limit: int = 5,
    available_tools: Iterable[str] | None = None,
    forbidden_tools: Iterable[str] | None = None,
):
    """Return recent successful TaskRun records in a matching experience context.

    `status='success'` remains an observed outcome, not a validated lesson. When
    environment constraints are supplied, recorded tool requirements are checked
    before a pattern is returned for reuse.
    """
    result = await graphiti.driver.execute_query(
        """
        MATCH (tr:TaskRun)
        WHERE tr.group_id = $gid
          AND tr.status = 'success'
          AND ($task_type IS NULL OR tr.task_type = $task_type)
          AND ($ctx IS NULL OR tr.context_hash = $ctx)
        OPTIONAL MATCH (tr)-[:HAS_TOOLCALL]->(tc:ToolCall)
        WITH tr, collect(DISTINCT tc.tool)[0..10] AS tools
        RETURN tr.uuid AS run_id,
               tr.task_type AS task_type,
               tr.goal AS goal,
               tr.repo AS repo,
               tr.project AS project,
               tr.context_hash AS context_hash,
               tr.ended_at AS ended_at,
               tr.duration_ms AS duration_ms,
               tr.quality_score AS quality_score,
               tr.tool_chain AS tool_chain,
               tools AS tools
        ORDER BY tr.ended_at DESC
        LIMIT $limit
        """,
        gid=_experience_group_id(),
        task_type=task_type,
        ctx=context_hash,
        limit=max(1, min(limit, 50)),
    )
    return filter_success_patterns_for_environment(
        (dict(record) for record in result.records),
        available_tools=available_tools,
        forbidden_tools=forbidden_tools,
    )


async def get_antipatterns(
    graphiti,
    *,
    task_type: str | None,
    context_hash: str | None,
    limit: int = 5,
):
    """Group failure patterns by error type and tool-chain hash."""
    result = await graphiti.driver.execute_query(
        """
        MATCH (tr:TaskRun)
        WHERE tr.group_id = $gid
          AND tr.status IN ['failure','timeout','aborted']
          AND ($task_type IS NULL OR tr.task_type = $task_type)
          AND ($ctx IS NULL OR tr.context_hash = $ctx)
        WITH tr.error_type AS error_type,
             tr.tool_chain_hash AS chain_hash,
             count(*) AS c,
             collect(DISTINCT tr.tool_chain)[0] AS example_chain,
             max(tr.ended_at) AS last_seen
        RETURN error_type, chain_hash, c, example_chain, last_seen
        ORDER BY c DESC, last_seen DESC
        LIMIT $limit
        """,
        gid=_experience_group_id(),
        task_type=task_type,
        ctx=context_hash,
        limit=max(1, min(limit, 50)),
    )
    return [dict(record) for record in result.records]
