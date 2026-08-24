"""Preview/apply provenance migration for legacy derived artifacts.

Default is preview. --apply mutates only READY plans; blocked L3 artifacts stay untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from core import get_graphiti_client
from core.provenance_migration import apply_ready_plan, scan_legacy_derived


async def run(apply: bool) -> int:
    graphiti = await get_graphiti_client().ensure_ready()
    plans = await scan_legacy_derived(graphiti)
    if apply:
        applied = []
        for plan in plans:
            if plan.get("status") == "READY":
                applied.append(await apply_ready_plan(graphiti, plan))
            else:
                applied.append(plan)
        plans = applied
    print(json.dumps({"mode": "APPLY" if apply else "DRY_RUN", "plans": plans}, ensure_ascii=False, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or apply legacy provenance migration")
    parser.add_argument("--apply", action="store_true", help="apply READY plans; default is dry-run")
    args = parser.parse_args()
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
