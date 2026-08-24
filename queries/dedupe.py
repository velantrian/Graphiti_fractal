#!/usr/bin/env python3
"""Namespace-safe Episodic deduplication utility.

Duplicates are considered equal only within the same group_id and only after
normalizing episode text. The default command soft-deletes duplicates; physical
purge is an explicit, separate option.
"""

import argparse
import asyncio
import hashlib
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from core.graphiti_client import get_graphiti_client


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


async def fetch_episodes(driver) -> list[dict]:
    res = await driver.execute_query(
        """
        MATCH (e:Episodic)
        WHERE coalesce(e.deleted, false) = false
        RETURN e.uuid AS uuid,
               coalesce(e.summary, e.content, e.episode_body, '') AS text,
               coalesce(e.group_id, 'unknown') AS group_id,
               e.created_at AS created_at,
               e.reference_time AS reference_time
        """
    )
    return [
        {
            "uuid": rec["uuid"],
            "text": rec["text"] or "",
            "group_id": rec["group_id"],
            "created_at": rec["created_at"],
            "reference_time": rec["reference_time"],
        }
        for rec in res.records
    ]


async def set_fingerprint(driver, uuid: str, fp: str) -> None:
    await driver.execute_query(
        "MATCH (e:Episodic {uuid:$uuid}) SET e.fingerprint=$fp",
        uuid=uuid,
        fp=fp,
    )


async def mark_duplicate(driver, uuid: str, master_uuid: str) -> None:
    await driver.execute_query(
        """
        MATCH (e:Episodic {uuid:$uuid})
        SET e.deleted=true, e.duplicate_of=$master, e.deleted_at=$deleted_at
        """,
        uuid=uuid,
        master=master_uuid,
        deleted_at=datetime.now(timezone.utc).isoformat(),
    )


async def purge_deleted(driver, days: int) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    res = await driver.execute_query(
        """
        MATCH (e:Episodic)
        WHERE e.deleted = true
          AND e.deleted_at IS NOT NULL
          AND datetime(e.deleted_at) < datetime($cutoff)
        DETACH DELETE e
        RETURN count(*) AS purged
        """,
        cutoff=cutoff.isoformat(),
    )
    return res.records[0]["purged"] if res.records else 0


def _master_sort_key(item: dict):
    # Prefer the oldest known source as the canonical copy, then stable UUID.
    ts = item.get("reference_time") or item.get("created_at")
    return (str(ts or ""), item["uuid"])


async def main(*, dry_run: bool = False, purge_days: int | None = None):
    graphiti = await get_graphiti_client().ensure_ready()
    driver = graphiti.driver
    episodes = await fetch_episodes(driver)

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for episode in episodes:
        fp = fingerprint(episode["text"])
        episode["fp"] = fp
        groups[(episode["group_id"], fp)].append(episode)

    duplicate_groups = [items for items in groups.values() if len(items) > 1]
    duplicates = sum(len(items) - 1 for items in duplicate_groups)
    print(f"Episodes scanned: {len(episodes)}")
    print(f"Namespace-scoped duplicate candidates: {duplicates}")

    if dry_run:
        print("✅ Dry run complete; no mutations applied")
        return

    fingerprints_set = 0
    duplicates_marked = 0
    for (_, fp), items in groups.items():
        for episode in items:
            await set_fingerprint(driver, episode["uuid"], fp)
            fingerprints_set += 1

        if len(items) <= 1:
            continue
        ordered = sorted(items, key=_master_sort_key)
        master_uuid = ordered[0]["uuid"]
        for duplicate in ordered[1:]:
            await mark_duplicate(driver, duplicate["uuid"], master_uuid)
            duplicates_marked += 1

    print(f"Fingerprints set: {fingerprints_set}")
    print(f"Duplicates soft-deleted: {duplicates_marked}")

    if purge_days is not None:
        if purge_days < 1:
            raise ValueError("purge_days must be >= 1")
        purged = await purge_deleted(driver, purge_days)
        print(f"Physically purged deleted episodes older than {purge_days} days: {purged}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Namespace-safe Episodic deduplication")
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not mutate")
    parser.add_argument(
        "--purge-deleted-days",
        type=int,
        default=None,
        help="Explicitly hard-delete already soft-deleted episodes older than N days",
    )
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run, purge_days=args.purge_deleted_days))
