"""Provider-backed E2E on an admitted disposable Neo4j.

Run with a fresh disposable database (Docker):

    make test-e2e             # admission check; provider round trip only with OPENAI_API_KEY
    make test-e2e-isolation   # plus decoy rejection evidence (what CI runs)

A plain ``pytest tests/e2e`` without FRACTAL_TEST_* never touches a database:
the tests skip (fail with FRACTAL_REQUIRE_TEST_NEO4J=1). See
docs/TEST_DATABASE_ISOLATION.md.
"""
import asyncio
import os
import uuid

import pytest
from neo4j import READ_ACCESS

from core.config import get_config
from core.graphiti_client import get_graphiti_client, reset_graphiti_client
from core.memory_ops import MemoryOps
from tests.support.neo4j_isolation import (
    MARKER_LABEL,
    graphiti_bound_to_admitted_driver,
    reset_test_graph,
)

INVENTORY_QUERY = (
    "MATCH (n) RETURN count(n) AS nodes, "
    f"count(CASE WHEN n:{MARKER_LABEL} THEN 1 END) AS markers"
)


async def _inventory(admitted):
    """Read-only snapshot: node counts plus schema rules."""
    async with admitted.driver.session(database=admitted.settings.database,
                                       default_access_mode=READ_ACCESS) as session:
        counts = await (await session.run(INVENTORY_QUERY)).single()
        constraints = [r["name"] async for r in await session.run("SHOW CONSTRAINTS YIELD name RETURN name")]
        indexes = [r["name"] async for r in await session.run(
            "SHOW INDEXES YIELD name, type WHERE type <> 'LOOKUP' RETURN name")]
    return {"nodes": counts["nodes"], "markers": counts["markers"],
            "constraints": sorted(constraints), "indexes": sorted(indexes)}


@pytest.mark.asyncio
async def test_admission_hands_graphiti_the_admitted_connection(admitted_neo4j):
    """Provider-free: the marker was read on the connection Graphiti receives, and nothing was written."""
    from graphiti_core.driver.neo4j_driver import Neo4jDriver

    before = await _inventory(admitted_neo4j)
    assert before["markers"] == 1

    settings = admitted_neo4j.settings
    with graphiti_bound_to_admitted_driver(admitted_neo4j.driver, settings) as handed_out:
        # Built in a worker thread (no running loop), graphiti_core schedules no schema task,
        # so this check itself writes nothing.
        graph_driver = await asyncio.to_thread(Neo4jDriver, settings.uri, settings.user, settings.password)
    assert graph_driver.client is admitted_neo4j.driver
    assert handed_out == [admitted_neo4j.driver]

    assert await _inventory(admitted_neo4j) == before


@pytest.mark.asyncio
async def test_provider_backed_ingest_and_recall_roundtrip(admitted_neo4j, monkeypatch):
    # Admission (fixture) has passed before anything here: no key is needed to reach this line.
    if not os.environ.get("OPENAI_API_KEY"):
        if os.environ.get("FRACTAL_REQUIRE_PROVIDER_E2E") == "1":
            pytest.fail("OPENAI_API_KEY is required for the provider-backed E2E run")
        pytest.skip("provider run skipped: OPENAI_API_KEY not set (database admission was verified)")

    settings = admitted_neo4j.settings
    for name, value in settings.runtime_environment().items():
        monkeypatch.setenv(name, value)
    get_config.cache_clear()
    reset_graphiti_client()

    with graphiti_bound_to_admitted_driver(admitted_neo4j.driver, settings):
        client = get_graphiti_client(force_new=True)
    assert client.raw.driver.client is admitted_neo4j.driver  # schema and writes use the admitted connection

    marker = f"fractal-e2e-{uuid.uuid4()}"
    text = f"The bounded provider-backed Fractal E2E marker is {marker}."
    try:
        graphiti = await client.ensure_ready()
        memory = MemoryOps(graphiti, os.environ.get("FRACTAL_USER_ID", "e2e-owner"))

        result = await memory.ingest_pipeline(
            text,
            source_description="provider_e2e",
            memory_type="knowledge",
        )
        assert result["status"] == "ok", result
        assert result["added"] >= 1, result

        recalled = await memory.search_memory(
            marker,
            scopes=["knowledge"],
            limit=10,
            include_episodes=True,
            include_entities=True,
        )
        episode_texts = [str(item.get("content") or "") for item in recalled.episodes]
        assert any(marker in content for content in episode_texts), recalled
    finally:
        try:
            await reset_test_graph(admitted_neo4j.driver, settings)  # re-verifies the marker, keeps it
        finally:
            await client.raw.close()
            reset_graphiti_client()
            get_config.cache_clear()

    # E2E asserts persistence + retrieval. It does not promote, authorize, or
    # transform the episode into a higher-authority fact.
