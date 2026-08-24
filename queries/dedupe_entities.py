#!/usr/bin/env python3
"""Namespace-safe Entity deduplication utility.

Entities are grouped only when both normalized name and group_id match.
Dry-run is the default operational recommendation; mutation must be explicit.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List

from core.graphiti_client import get_graphiti_client
from core.text_utils import normalize_entity_name, is_meaningful_entity_name

logger = logging.getLogger(__name__)


async def fetch_entities(driver) -> List[Dict]:
    res = await driver.execute_query(
        """
        MATCH (e:Entity)
        WHERE coalesce(e.deleted, false) = false
          AND e.group_id IS NOT NULL
        RETURN e.uuid AS uuid, coalesce(e.name, '') AS name, e.group_id AS group_id
        """
    )
    entities = []
    for rec in res.records:
        name = rec["name"] or ""
        if not is_meaningful_entity_name(name):
            continue
        entities.append(
            {
                "uuid": rec["uuid"],
                "name": name,
                "normalized_name": normalize_entity_name(name),
                "group_id": rec["group_id"],
            }
        )
    return entities


async def fetch_entity_relationships(driver, entity_uuid: str) -> Dict[str, List[dict]]:
    outgoing_res = await driver.execute_query(
        """
        MATCH (e:Entity {uuid: $uuid})-[r]->(target)
        RETURN type(r) AS rel_type, target.uuid AS target_uuid
        """,
        uuid=entity_uuid,
    )
    incoming_res = await driver.execute_query(
        """
        MATCH (source)-[r]->(e:Entity {uuid: $uuid})
        RETURN type(r) AS rel_type, source.uuid AS source_uuid
        """,
        uuid=entity_uuid,
    )
    return {
        "outgoing": [
            {"rel_type": rec["rel_type"], "target_uuid": rec["target_uuid"]}
            for rec in outgoing_res.records
        ],
        "incoming": [
            {"rel_type": rec["rel_type"], "source_uuid": rec["source_uuid"]}
            for rec in incoming_res.records
        ],
    }


async def merge_entity_properties(driver, from_uuids: List[str], to_uuid: str) -> None:
    all_summaries = set()
    all_tags = set()

    for entity_uuid in [*from_uuids, to_uuid]:
        res = await driver.execute_query(
            """
            MATCH (e:Entity {uuid: $uuid})
            RETURN e.summary AS summary, e.tags AS tags
            """,
            uuid=entity_uuid,
        )
        if not res.records:
            continue
        rec = res.records[0]
        if rec["summary"]:
            all_summaries.add(rec["summary"])
        if rec["tags"] and isinstance(rec["tags"], list):
            all_tags.update(rec["tags"])

    if all_summaries:
        await driver.execute_query(
            "MATCH (e:Entity {uuid: $uuid}) SET e.summary = $summary",
            uuid=to_uuid,
            summary=" | ".join(sorted(all_summaries)),
        )
    if all_tags:
        await driver.execute_query(
            "MATCH (e:Entity {uuid: $uuid}) SET e.tags = $tags",
            uuid=to_uuid,
            tags=sorted(all_tags),
        )


async def merge_entity_relationships(driver, from_uuid: str, to_uuid: str) -> None:
    # Neo4j 5.26+ supports dynamic relationship types via $(expression).
    await driver.execute_query(
        """
        MATCH (from:Entity {uuid: $from_uuid})-[r]->(target)
        WHERE target.uuid <> $to_uuid
        MERGE (to:Entity {uuid: $to_uuid})-[r2:$(type(r))]->(target)
        SET r2 += properties(r)
        DELETE r
        """,
        from_uuid=from_uuid,
        to_uuid=to_uuid,
    )
    await driver.execute_query(
        """
        MATCH (source)-[r]->(from:Entity {uuid: $from_uuid})
        WHERE source.uuid <> $to_uuid
        MERGE (source)-[r2:$(type(r))]->(to:Entity {uuid: $to_uuid})
        SET r2 += properties(r)
        DELETE r
        """,
        from_uuid=from_uuid,
        to_uuid=to_uuid,
    )


async def mark_entity_deleted(driver, uuid: str, merged_into: str) -> None:
    await driver.execute_query(
        """
        MATCH (e:Entity {uuid: $uuid})
        SET e.deleted = true,
            e.deleted_at = $deleted_at,
            e.merged_into = $merged_into
        """,
        uuid=uuid,
        deleted_at=datetime.now(timezone.utc).isoformat(),
        merged_into=merged_into,
    )


def group_entities(entities: List[Dict]) -> dict[tuple[str, str], List[Dict]]:
    groups: dict[tuple[str, str], List[Dict]] = defaultdict(list)
    for entity in entities:
        groups[(entity["normalized_name"], entity["group_id"])].append(entity)
    return groups


async def deduplicate_entities(driver, entities: List[Dict]) -> Dict[str, int]:
    groups = group_entities(entities)
    stats = {
        "total_entities": len(entities),
        "unique_groups": len(groups),
        "duplicates_found": 0,
        "entities_merged": 0,
        "relationships_transferred": 0,
    }

    for (normalized_name, group_id), group in groups.items():
        if len(group) <= 1:
            continue

        stats["duplicates_found"] += len(group) - 1
        master = min(group, key=lambda item: item["uuid"])
        duplicates = [item for item in group if item["uuid"] != master["uuid"]]

        logger.info(
            "Dedup group %r namespace=%s master=%s duplicates=%d",
            normalized_name,
            group_id,
            master["uuid"],
            len(duplicates),
        )

        await merge_entity_properties(
            driver,
            [item["uuid"] for item in duplicates],
            master["uuid"],
        )

        for duplicate in duplicates:
            relationships = await fetch_entity_relationships(driver, duplicate["uuid"])
            await merge_entity_relationships(driver, duplicate["uuid"], master["uuid"])
            await mark_entity_deleted(driver, duplicate["uuid"], master["uuid"])
            stats["entities_merged"] += 1
            stats["relationships_transferred"] += len(relationships["incoming"]) + len(
                relationships["outgoing"]
            )

    return stats


async def main(dry_run: bool = False):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    graphiti = await get_graphiti_client().ensure_ready()
    driver = graphiti.driver
    entities = await fetch_entities(driver)
    groups = group_entities(entities)
    duplicates_total = sum(len(group) - 1 for group in groups.values() if len(group) > 1)

    logger.info("Entities: %d", len(entities))
    logger.info("Unique (normalized_name, group_id) groups: %d", len(groups))
    logger.info("Potential duplicates: %d", duplicates_total)

    for index, ((normalized_name, group_id), group) in enumerate(
        sorted(groups.items(), key=lambda item: len(item[1]), reverse=True)[:10], 1
    ):
        if len(group) > 1:
            logger.info(
                "%d. %r namespace=%s entities=%d",
                index,
                normalized_name,
                group_id,
                len(group),
            )

    if dry_run:
        logger.info("✅ Dry run complete; no mutations applied")
        return

    stats = await deduplicate_entities(driver, entities)
    logger.info("✅ Entity dedup complete: %s", stats)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Дедупликация Entity узлов")
    parser.add_argument("--dry-run", action="store_true", help="Анализ без применения изменений")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
