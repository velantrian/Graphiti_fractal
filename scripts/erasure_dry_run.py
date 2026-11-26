#!/usr/bin/env python
"""Read-only report on legacy deleted=true flags: why and how much would be restricted.

  NEO4J_URI=... NEO4J_USER=... NEO4J_PASSWORD=... python scripts/erasure_dry_run.py [--json]

Uses READ-access queries only (Neo4j itself rejects writes in that mode). It never
migrates anything; see docs/ERASURE_VARIANT_B.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from neo4j import AsyncGraphDatabase, RoutingControl  # noqa: E402

from core.erasure_dry_run import dry_run  # noqa: E402


class _Result:
    def __init__(self, records):
        self.records = records


class ReadOnlyGraph:
    """Graphiti-shaped adapter whose every statement runs in READ access mode."""

    def __init__(self, driver, database: str = "neo4j"):
        self._driver, self._database = driver, database
        self.driver = self

    async def execute_query(self, query, **params):
        records, _, _ = await self._driver.execute_query(
            query, parameters_=params, database_=self._database, routing_=RoutingControl.READ)
        return _Result(records)


def render(report: dict) -> str:
    r = report["reasons"]
    lines = [
        f"flagged episodes: {report['flagged_episodes']}; flagged objects by label/group: "
        + ", ".join(f"{x['label']}/{x['grp']}={x['n']}" for x in report["legacy_flagged_objects"]) or "none",
        f"LEGACY_FLAG_NOT_MIGRATED: {r['LEGACY_FLAG_NOT_MIGRATED']['objects_hidden_today']} hidden today; "
        f"{r['LEGACY_FLAG_NOT_MIGRATED']['derived_objects_visible_today_that_migration_would_restrict']} derived objects "
        "still visible that migration would restrict",
        f"AREA_AFTER_MIGRATION: {json.dumps(r['AREA_AFTER_MIGRATION'], sort_keys=True)}",
        f"GROUP_WRITE_PAUSE: {r['GROUP_WRITE_PAUSE']}",
        f"UNKNOWN_LINEAGE_DERIVED_KIND: {r['UNKNOWN_LINEAGE_DERIVED_KIND']['total']} "
        f"({json.dumps(r['UNKNOWN_LINEAGE_DERIVED_KIND']['by_group_and_kind'])})",
        f"LINEAGE_GAP: {r['LINEAGE_GAP']['total']} objects, content UNKNOWN (not read; not restricted; an erasure cannot reach them)",
        f"TOMBSTONE_WITHOUT_SEED: {r['TOMBSTONE_WITHOUT_SEED']['total']}",
        f"groups touched after migration: {report['groups_touched_after_migration']}; "
        f"untouched: {report['groups_untouched_after_migration']}",
        report["migration"],
    ]
    return "\n".join(lines)


async def main(as_json: bool, details: bool) -> int:
    uri, user, password = os.environ.get("NEO4J_URI"), os.environ.get("NEO4J_USER"), os.environ.get("NEO4J_PASSWORD")
    if not (uri and user and password):
        print("NEO4J_URI, NEO4J_USER and NEO4J_PASSWORD are required (read-only access is enough)", file=sys.stderr)
        return 2
    driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    try:
        report = await dry_run(ReadOnlyGraph(driver, os.environ.get("NEO4J_DATABASE") or "neo4j"), details=details)
    finally:
        await driver.close()
    print(json.dumps(report, indent=2, sort_keys=True, default=str) if as_json else render(report))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--details", action="store_true", help="also list the objects (uuid, group, label; no content)")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.json or args.details, args.details)))
