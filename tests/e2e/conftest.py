"""Admission for the provider-backed E2E: disposable Neo4j only, verified first.

Same rule as tests/integration (docs/TEST_DATABASE_ISOLATION.md), with one
addition: the marker is verified on the AsyncDriver that Graphiti itself will
use (``graphiti_bound_to_admitted_driver``), not on a separate throwaway
connection. Order inside every test:

1. configuration only from FRACTAL_TEST_NEO4J_* / FRACTAL_TEST_RUN_ID; runtime
   NEO4J_* and .env are never a fallback;
2. read-only marker check on the driver's own pool;
3. only then may a Graphiti client be built on that driver (schema, writes).

Without test configuration the tests skip (an error with
FRACTAL_REQUIRE_TEST_NEO4J=1). A rejected database fails every test of the
session; the rejection is remembered, so the database receives exactly one
read-only query and nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio

from tests.support.neo4j_isolation import (
    IsolatedNeo4jSettings,
    IsolationNotConfigured,
    IsolationRejected,
    open_admitted_async_driver,
    require_test_database,
)


@dataclass
class _SessionAdmission:
    settings: IsolatedNeo4jSettings
    rejection: str | None = None


@dataclass(frozen=True)
class AdmittedNeo4j:
    settings: IsolatedNeo4jSettings
    driver: Any


@pytest.fixture(scope="session")
def e2e_neo4j_settings() -> _SessionAdmission:
    try:
        settings = IsolatedNeo4jSettings.from_env()
    except IsolationNotConfigured as exc:
        if require_test_database():
            pytest.fail(str(exc), pytrace=False)
        pytest.skip(str(exc))
    except IsolationRejected as exc:
        pytest.fail(f"isolation guard rejected test configuration: {exc}", pytrace=False)
    return _SessionAdmission(settings)


@pytest_asyncio.fixture
async def admitted_neo4j(e2e_neo4j_settings):
    """An AsyncDriver whose own pool passed the read-only marker check."""
    state = e2e_neo4j_settings
    if state.rejection is not None:
        pytest.fail(state.rejection, pytrace=False)
    try:
        driver = await open_admitted_async_driver(state.settings)
    except IsolationRejected as exc:
        state.rejection = f"isolation guard rejected test database: {exc}"
        pytest.fail(state.rejection, pytrace=False)
    try:
        yield AdmittedNeo4j(state.settings, driver)
    finally:
        await driver.close()
