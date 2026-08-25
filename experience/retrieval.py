from __future__ import annotations

from collections.abc import Iterable

from core.config import get_config


SUCCESS_PATTERN_PAGE_SIZE = 50


def _experience_group_id() -> str:
    return get_config().memory.experience_group_id


def _normalized_names(values: Iterable[str] | None) -> set[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        values = [values]
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _recorded_required_tools(pattern: dict) -> tuple[set[str], bool]:
    """Return the full recorded tool footprint and whether it is known.

    `tools` is a read-side projection of ToolCall nodes and may be display-bounded
    or incomplete for legacy rows. `tool_chain` is the TaskRun's recorded chain.
    Applicability therefore uses the union of both sources rather than allowing
    either source to shadow the other.

    Historical empty/missing values remain ambiguous: they may mean a run used no
    tools, or that tool provenance was not recorded. Constrained reuse treats that
    ambiguity as unknown rather than proving an empty requirement set.
    """
    tools = _normalized_names(pattern.get("tools")) or set()
    tool_chain = _normalized_names(pattern.get("tool_chain")) or set()
    required = tools | tool_chain
    return required, bool(required)


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
        reason = "recorded tool requirements incompatible with supplied constraints"
    else:
        applicable = True
        reason = "recorded tool requirements compatible with supplied constraints"

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


async def _read_success_pattern_page(
    graphiti,
    *,
    task_type: str | None,
    context_hash: str | None,
    offset: int,
    page_size: int,
) -> list[dict]:
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
        ORDER BY tr.ended_at DESC, tr.uuid ASC
        SKIP $offset
        LIMIT $limit
        """,
        gid=_experience_group_id(),
        task_type=task_type,
        ctx=context_hash,
        offset=max(0, offset),
        limit=max(1, min(page_size, SUCCESS_PATTERN_PAGE_SIZE)),
    )
    return [dict(record) for record in result.records]


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

    Constrained retrieval paginates through the ordered matching history until it
    has collected the requested number of applicable rows or exhausts that history.
    The final result limit is therefore applied after applicability filtering.
    """
    requested_limit = max(1, min(limit, 50))
    constrained = available_tools is not None or forbidden_tools is not None

    if not constrained:
        return await _read_success_pattern_page(
            graphiti,
            task_type=task_type,
            context_hash=context_hash,
            offset=0,
            page_size=requested_limit,
        )

    applicable_rows: list[dict] = []
    offset = 0

    while len(applicable_rows) < requested_limit:
        page = await _read_success_pattern_page(
            graphiti,
            task_type=task_type,
            context_hash=context_hash,
            offset=offset,
            page_size=SUCCESS_PATTERN_PAGE_SIZE,
        )
        if not page:
            break

        applicable_rows.extend(
            filter_success_patterns_for_environment(
                page,
                available_tools=available_tools,
                forbidden_tools=forbidden_tools,
            )
        )

        offset += len(page)
        if len(page) < SUCCESS_PATTERN_PAGE_SIZE:
            break

    return applicable_rows[:requested_limit]


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
