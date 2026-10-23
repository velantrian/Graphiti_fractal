"""Regression tests for fail-closed Graphiti memory integrity boundaries."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from neo4j.exceptions import ConstraintError

from core.ingest_atomicity import acquire_ingest_claim
from core.memory_ops import MemoryOps


def test_explicit_empty_scopes_do_not_widen_search():
    graphiti = SimpleNamespace(search_=AsyncMock())
    memory = MemoryOps(graphiti, "owner")
    with pytest.raises(ValueError, match="empty scopes"):
        asyncio.run(memory.search_memory("private query", scopes=[]))
    graphiti.search_.assert_not_awaited()


class _ClaimDriver:
    def __init__(self, error):
        self.error = error
        self.calls = 0

    async def execute_query(self, query, **kwargs):
        self.calls += 1
        if "CREATE (c:FractalIngestClaim" in query:
            raise self.error
        return SimpleNamespace(records=[])


def test_ingest_claim_unexpected_database_failure_propagates():
    driver = _ClaimDriver(RuntimeError("database unavailable"))
    graphiti = SimpleNamespace(driver=driver)
    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(acquire_ingest_claim(graphiti, group_id="project", fingerprint="abc"))


def test_ingest_claim_unique_conflict_can_be_skipped():
    driver = _ClaimDriver(ConstraintError("unique claim already exists"))
    graphiti = SimpleNamespace(driver=driver)
    assert asyncio.run(acquire_ingest_claim(graphiti, group_id="project", fingerprint="abc")) is None
