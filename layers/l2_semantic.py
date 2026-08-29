import asyncio
import logging
from collections.abc import Sequence

from core import get_graphiti_client

logger = logging.getLogger(__name__)

# L2 synthesis is allowed to read only the same trusted durable namespaces used
# by canonical MemoryOps retrieval. External imports remain quarantined in the
# dedicated `imports` group and must never be silently promoted through L2/L3.
DEFAULT_L2_GROUP_IDS = ("personal", "project", "knowledge", "experience")


async def trigger_community_build(graphiti):
    """Rebuild Graphiti communities; intended for scheduled maintenance, not requests."""
    try:
        logger.info("Starting L2 community build...")
        await graphiti.build_communities()
        logger.info("L2 community build completed.")
    except Exception as exc:
        logger.error("Failed to build communities: %s", exc)
        raise


def _normalize_allowed_groups(allowed_group_ids: Sequence[str] | None) -> list[str]:
    groups = list(allowed_group_ids or DEFAULT_L2_GROUP_IDS)
    groups = [str(group).strip() for group in groups if str(group).strip()]
    if not groups:
        raise ValueError("L2 semantic context requires at least one allowed group")
    if "imports" in groups:
        raise ValueError("L2 semantic synthesis cannot include quarantined imports")
    return list(dict.fromkeys(groups))


async def get_l2_semantic_context_with_sources(
    graphiti,
    entity_name: str,
    *,
    allowed_group_ids: Sequence[str] | None = None,
) -> tuple[str | None, list[str]]:
    """Retrieve trusted L2 community context with exact source community UUIDs.

    The query is fail-closed against the quarantined ``imports`` namespace.
    Callers may narrow the trusted namespaces, but cannot opt imports into L2.
    """
    driver = getattr(graphiti, "driver", None) or getattr(graphiti, "_driver", None)
    if not driver:
        return "Graphiti driver not found for L2 context.", []

    allowed_groups = _normalize_allowed_groups(allowed_group_ids)
    query = """
    MATCH (e:Entity)
    WHERE toLower(e.name) CONTAINS toLower($name)
      AND coalesce(e.deleted, false) = false
      AND e.group_id IN $allowed_groups
      AND coalesce(e.origin_class, 'trusted') <> 'untrusted'
    MATCH (c:Community)-[:HAS_MEMBER]->(e)
    WHERE coalesce(c.deleted, false) = false
      AND coalesce(c.group_id, e.group_id) IN $allowed_groups
      AND coalesce(c.origin_class, 'trusted') <> 'untrusted'
    RETURN DISTINCT c.uuid AS uuid,
           c.name AS name,
           c.summary AS summary,
           c.level AS level
    ORDER BY coalesce(c.level, 0) ASC
    LIMIT 5
    """

    try:
        if hasattr(driver, "execute_query"):
            res = await driver.execute_query(
                query,
                name=entity_name,
                allowed_groups=allowed_groups,
            )
            records = res.records
        else:
            async with driver.session() as session:
                res = await session.run(
                    query,
                    name=entity_name,
                    allowed_groups=allowed_groups,
                )
                records = await res.list()
    except Exception as exc:
        logger.warning("L2 community query failed: %s", exc)
        return "L2 Context: community structure unavailable; run build_communities and retry.", []

    if not records:
        return None, []

    lines = [f"🧠 L2 Semantic Context (Communities) for '{entity_name}':", ""]
    source_ids: list[str] = []
    for rec in records:
        c_uuid = rec["uuid"]
        if c_uuid:
            source_ids.append(str(c_uuid))
        c_name = rec["name"] or "Unnamed Community"
        c_sum = rec["summary"] or "No summary available."
        c_level = rec.get("level") if hasattr(rec, "get") else rec["level"]
        if c_level is None:
            c_level = "?"
        lines.append(f"=== Community: {c_name} (Level {c_level}) ===")
        lines.append(c_sum)
        lines.append("")

    return "\n".join(lines).rstrip(), source_ids


async def get_l2_semantic_context(
    graphiti,
    entity_name: str,
    *,
    allowed_group_ids: Sequence[str] | None = None,
) -> str | None:
    """Backward-compatible context-only wrapper with the same trust boundary."""
    context, _ = await get_l2_semantic_context_with_sources(
        graphiti,
        entity_name,
        allowed_group_ids=allowed_group_ids,
    )
    return context


async def test_l2():
    graphiti_client = get_graphiti_client()
    graphiti = await graphiti_client.ensure_ready()
    context = await get_l2_semantic_context(graphiti, "Sergey")
    print(context)


if __name__ == "__main__":
    asyncio.run(test_l2())
