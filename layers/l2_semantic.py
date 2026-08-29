import asyncio
import logging
from collections.abc import Sequence

from core import get_graphiti_client
from core.instance import get_instance_user_id

logger = logging.getLogger(__name__)

# L2 synthesis is allowed to read only these owner-controlled durable namespaces.
# Callers may narrow this set, but may never expand it.
DEFAULT_L2_GROUP_IDS = ("personal", "project", "knowledge", "experience")
MAX_L2_SOURCES_PER_COMMUNITY = 6
MAX_L2_SOURCE_CHARS = 600


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
    """Return a strict subset of the L2 trust allow-list.

    ``None`` means the documented default set. An explicit empty collection is
    rejected instead of silently reopening all default namespaces.
    """
    if allowed_group_ids is None:
        return list(DEFAULT_L2_GROUP_IDS)

    groups = [str(group).strip() for group in allowed_group_ids if str(group).strip()]
    groups = list(dict.fromkeys(groups))
    if not groups:
        raise ValueError("L2 semantic context requires at least one allowed group")

    unsupported = sorted(set(groups) - set(DEFAULT_L2_GROUP_IDS))
    if unsupported:
        raise ValueError(
            "L2 semantic synthesis cannot expand the trusted group allow-list: "
            + ", ".join(unsupported)
        )
    return groups


async def get_l2_semantic_context_with_sources(
    graphiti,
    entity_name: str,
    *,
    allowed_group_ids: Sequence[str] | None = None,
) -> tuple[str | None, list[str]]:
    """Retrieve fail-closed owner-derived L2 context with exact source UUIDs.

    Community nodes are used only as structural grouping. Their generated
    summaries are deliberately not trusted as semantic input. The rendered L2
    context comes from source Episodic content whose provenance is either
    explicitly ``origin_class=owner`` or a legacy owner-authored episode with no
    origin_class. Missing origin without owner authorship is denied.

    Any current non-owner provenance, cross-group provenance, or durable
    ``has_non_owner_source`` taint on a member excludes the whole community.
    """
    driver = getattr(graphiti, "driver", None) or getattr(graphiti, "_driver", None)
    if not driver:
        return "Graphiti driver not found for L2 context.", []

    allowed_groups = _normalize_allowed_groups(allowed_group_ids)
    owner_id = get_instance_user_id()
    query = """
    MATCH (matched:Entity)
    WHERE toLower(matched.name) CONTAINS toLower($name)
      AND coalesce(matched.deleted, false) = false
      AND matched.group_id IN $allowed_groups
    MATCH (c:Community)-[:HAS_MEMBER]->(matched)
    WHERE coalesce(c.deleted, false) = false
      AND c.group_id IN $allowed_groups
      AND c.group_id = matched.group_id
    WITH DISTINCT c
    ORDER BY coalesce(c.level, 0) ASC
    LIMIT 5

    MATCH (c)-[:HAS_MEMBER]->(member:Entity)
    WHERE coalesce(member.deleted, false) = false
      AND member.group_id = c.group_id
    OPTIONAL MATCH (source:Episodic)-[:MENTIONS]->(member)
    WHERE coalesce(source.deleted, false) = false
    OPTIONAL MATCH (owner:User {user_id:$owner_id})-[:AUTHORED]->(source)
    WITH c, member,
         [p IN collect(DISTINCT {
             uuid: source.uuid,
             group_id: source.group_id,
             origin_class: source.origin_class,
             source_description: source.source_description,
             content: coalesce(source.content, source.episode_body, ''),
             owner_authored: owner IS NOT NULL
         }) WHERE p.uuid IS NOT NULL] AS provenance
    WITH c, collect({
        uuid: member.uuid,
        tainted: coalesce(member.has_non_owner_source, false),
        provenance: provenance
    }) AS members
    WHERE all(member IN members WHERE
        member.tainted = false
        AND size(member.provenance) > 0
        AND all(p IN member.provenance WHERE
            p.group_id = c.group_id
            AND (
                p.origin_class = 'owner'
                OR (
                    p.origin_class IS NULL
                    AND p.owner_authored = true
                    AND NOT coalesce(p.source_description, '') STARTS WITH 'l3_profile:'
                )
            )
        )
    )
    RETURN c.uuid AS uuid,
           c.level AS level,
           members AS members
    ORDER BY coalesce(c.level, 0) ASC
    """

    try:
        if hasattr(driver, "execute_query"):
            res = await driver.execute_query(
                query,
                name=entity_name,
                allowed_groups=allowed_groups,
                owner_id=owner_id,
            )
            records = res.records
        else:
            async with driver.session() as session:
                res = await session.run(
                    query,
                    name=entity_name,
                    allowed_groups=allowed_groups,
                    owner_id=owner_id,
                )
                records = await res.list()
    except Exception as exc:
        logger.warning("L2 community query failed: %s", exc)
        return "L2 Context: trusted community structure unavailable; rebuild/repair provenance and retry.", []

    if not records:
        return None, []

    lines = [f"🧠 L2 Trusted Context for '{entity_name}':", ""]
    source_ids: list[str] = []
    seen_sources: set[str] = set()

    for record in records:
        community_uuid = str(record["uuid"]) if record["uuid"] else ""
        level = record.get("level") if hasattr(record, "get") else record["level"]
        if level is None:
            level = "?"
        if community_uuid:
            source_ids.append(community_uuid)
        lines.append(f"=== Community {community_uuid[:8] or 'unknown'} (Level {level}) ===")

        emitted = 0
        for member in record["members"] or []:
            for provenance in member.get("provenance", []) or []:
                source_uuid = str(provenance.get("uuid") or "")
                content = str(provenance.get("content") or "").strip()
                if not source_uuid or not content or source_uuid in seen_sources:
                    continue
                seen_sources.add(source_uuid)
                source_ids.append(source_uuid)
                lines.append(f"- {content[:MAX_L2_SOURCE_CHARS]}")
                emitted += 1
                if emitted >= MAX_L2_SOURCES_PER_COMMUNITY:
                    break
            if emitted >= MAX_L2_SOURCES_PER_COMMUNITY:
                break
        lines.append("")

    if not seen_sources:
        return None, []
    return "\n".join(lines).rstrip(), list(dict.fromkeys(source_ids))


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
