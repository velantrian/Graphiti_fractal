from __future__ import annotations

from collections.abc import Iterable

from core.config import get_config


MAX_SUCCESS_PATTERN_CANDIDATES = 50


def _experience_group_id() -> str:
    return get_config().memory.experience_group_id


def _normalized_names(values: Iterable[str] | None) -> set[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _recorded_required_tools(pattern: dict) -> tuple[set[str], bool]:
    """Return recorded tool requirements and whether that footprint is known.

    Historical empty/missing tool fields are ambiguous: they may mean a run used
    no tools, or that tool provenance was not recorded. Constrained reuse treats
    that ambiguity as unknown rather than proving an empty requirement set.
    """
    tools = _normalized_names(pattern.get("tools"))
    if tools:
        return tools, True

    tool_chain = _normalized_names(pattern.get("tool_chain"))
    if tool_chain:
        return tool_chain, True

    return set(), False


def assess_pattern_applicability(
    pattern: dict,
    *,
    available_tools: Iterable[str] | None = None,
    forbidden_tools: Iterable[str] | None = None,
) -> dict:
    """Return a deterministic environment-applicability assessment.

    This is deliberately narrow: it only reasons about tools recorded on the
    prior TaskRun. It does not claim semantic, causal, or epistemic validity.
    Unknown historical tool provenance fails closed when reuse is constrained.
    """
    required, tool_requirement_known = _recorded_required_tools(pattern)
    available = _normalized_names(available_tools)
    forbidden = _normalized_names(forbidden_tools) or set()

    constrained = available is not None or forbidden_tools is not None
    missing = (
        sorted(required - available)
        if tool_requirement_known and available is not None
        else []
    )
    blocked = sorted(required & forbidden) if tool_requirement_known else []

    if not constrained:
        applicable = True
        reason = "no environment constraint supplied"
    elif not tool_requirement_known:
        applicable = False
        reason = "tool requirements unknown"
    elif missing or blocked:
        applicable = False
        reason = "environment mismatch"
    else:
        applicable = True
        reason = "tool requirements satisfied"

    return {
        "applicable": applicable,
        "constrained": constrained,
        "tool_requirement_known": tool_requirement_known,
        "required_tools": sorted(required),
        "missing_tools": missing,
        "forbidden_tools": blocked,
        "reason": reason,
    }


def filter_success_patterns_for_environment(
    patterns: Iterable[dict],
    *,
    available_tools: Iterable[str] | None = None,
    forbidden_tools: Iterable[str] | None = None,
) -> list[dict]:
    """Filter prior success observations before reuse in a constrained environment.

    When no environment constraint is supplied, legacy behavior is preserved.
    When a constraint is supplied, inapplicable or provenance-unknown patterns
    fail closed and every returned row carries an explicit applicability receipt.
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
    requested_limit = max(1, min(limit, MAX_SUCCESS_PATTERN_CANDIDATES))
    constrained = available_tools is not None or forbidden_tools is not None

    # Applicability is evaluated after the Neo4j read. In constrained mode scan
    # the full existing bounded candidate window first so recent inapplicable
    # rows do not starve applicable rows that are still within the supported cap.
    candidate_limit = MAX_SUCCESS_PATTERN_CANDIDATES if constrained else requested_limit

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
        limit=candidate_limit,
    )
    rows = filter_success_patterns_for_environment(
        (dict(record) for record in result.records),
        available_tools=available_tools,
        forbidden_tools=forbidden_tools,
    )
    return rows[:requested_limit]


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
