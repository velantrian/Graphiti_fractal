"""Child process for crash tests: ingest one text, dying hard at a chosen point.

  python -m tests.support.ingest_crash_child <before-send|inside-send|none> <group> <text>

Uses FRACTAL_TEST_* (admitted database) only. ``os._exit`` skips every finally,
exception handler and atexit hook, like a killed process.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from tests.support.graphiti_stub import StubDriver, StubGraphiti
from tests.support.neo4j_isolation import IsolatedNeo4jSettings, open_admitted_async_driver


class CrashSemaphore:
    async def __aenter__(self):
        os._exit(137)

    async def __aexit__(self, *exc_info):
        return False


async def crash_in_add_episode(_stub, _kwargs):
    os._exit(137)


async def main(mode: str, group: str, text: str) -> None:
    import knowledge.ingest as ingest

    async def no_embedding(_text):
        return None

    ingest.get_embedding = no_embedding
    settings = IsolatedNeo4jSettings.from_env()
    driver = await open_admitted_async_driver(settings)
    stub = StubGraphiti(StubDriver(driver, settings.database),
                        [crash_in_add_episode] if mode == "inside-send" else [])
    if mode == "before-send":
        ingest.get_write_semaphore = lambda: CrashSemaphore()
    result = await ingest.ingest_text_document(stub, text, group_id=group, user_id="child-owner")
    print(json.dumps({"completeness": result["completeness"], "calls": len(stub.calls)}))
    await driver.close()


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:4]))
