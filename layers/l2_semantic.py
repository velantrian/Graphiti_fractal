import asyncio
import logging

from core import get_graphiti_client

logger = logging.getLogger(__name__)


async def trigger_community_build(graphiti):
    """Rebuild Graphiti communities; intended for scheduled maintenance, not requests."""
    try:
        logger.info("Starting L2 community build...")
        await graphiti.build_communities()
        logger.info("L2 community build completed.")
    except Exception as exc:
        logger.error("Failed to build communities: %s", exc)
        raise


async def get_l2_semantic_context(graphiti, entity_name: str) -> str | None:
    """Retrieve Graphiti community summaries for a matching entity."""
    driver = getattr(graphiti, "driver", None) or getattr(graphiti, "_driver", None)
    if not driver:
        return "Graphiti driver not found for L2 context."

    # Graphiti 0.29.x models community membership as
    # (:Community)-[:HAS_MEMBER]->(:Entity).
    query = """
    MATCH (e:Entity)
    WHERE toLower(e.name) CONTAINS toLower($name)
      AND coalesce(e.deleted, false) = false
    MATCH (c:Community)-[:HAS_MEMBER]->(e)
    WHERE coalesce(c.deleted, false) = false
    RETURN DISTINCT c.uuid AS uuid,
           c.name AS name,
           c.summary AS summary,
           c.level AS level
    ORDER BY coalesce(c.level, 0) ASC
    LIMIT 5
    """

    try:
        if hasattr(driver, "execute_query"):
            res = await driver.execute_query(query, name=entity_name)
            records = res.records
        else:
            async with driver.session() as session:
                res = await session.run(query, name=entity_name)
                records = await res.list()
    except Exception as exc:
        logger.warning("L2 community query failed: %s", exc)
        return "L2 Context: community structure unavailable; run build_communities and retry."

    if not records:
        return None

    lines = [f"🧠 L2 Semantic Context (Communities) for '{entity_name}':", ""]
    for rec in records:
        c_name = rec["name"] or "Unnamed Community"
        c_sum = rec["summary"] or "No summary available."
        c_level = rec.get("level") if hasattr(rec, "get") else rec["level"]
        if c_level is None:
            c_level = "?"
        lines.append(f"=== Community: {c_name} (Level {c_level}) ===")
        lines.append(c_sum)
        lines.append("")

    return "\n".join(lines).rstrip()


async def test_l2():
    graphiti_client = get_graphiti_client()
    graphiti = await graphiti_client.ensure_ready()
    context = await get_l2_semantic_context(graphiti, "Sergey")
    print(context)


if __name__ == "__main__":
    asyncio.run(test_l2())
