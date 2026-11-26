"""
Pytest fixtures for integration tests.

Every test in this directory runs against a disposable Neo4j admitted by
tests/support/neo4j_isolation.py, unless it is marked ``no_neo4j``:

* configuration comes only from FRACTAL_TEST_NEO4J_* / FRACTAL_TEST_RUN_ID;
* the marker is verified read-only once per session, *before* any test can
  build a Graphiti client (whose constructor schedules schema writes);
* only then are NEO4J_* pointed at the admitted database for application code.

Without test configuration the tests are skipped (an error when
FRACTAL_REQUIRE_TEST_NEO4J=1, as in CI). A configured but unverifiable
database is always an error, and nothing is written to it.
"""

import pytest
import pytest_asyncio

from tests.support.neo4j_isolation import (
    IsolatedNeo4jSettings,
    IsolationNotConfigured,
    IsolationRejected,
    admit_sync,
    open_admitted_async_driver,
    require_test_database,
    reset_test_graph,
)


@pytest.fixture(scope="session")
def isolated_neo4j_settings():
    """Read-only admission of the configured test database, once per session."""
    try:
        settings = IsolatedNeo4jSettings.from_env()
    except IsolationNotConfigured as exc:
        if require_test_database():
            pytest.fail(str(exc), pytrace=False)
        pytest.skip(str(exc))
    except IsolationRejected as exc:
        pytest.fail(f"isolation guard rejected test configuration: {exc}", pytrace=False)
    try:
        admit_sync(settings)
    except IsolationRejected as exc:
        pytest.fail(f"isolation guard rejected test database: {exc}", pytrace=False)
    return settings


@pytest.fixture(autouse=True)
def neo4j_isolation(request, monkeypatch):
    """Point application NEO4J_* settings at the admitted database only."""
    if request.node.get_closest_marker("no_neo4j"):
        yield None
        return
    settings = request.getfixturevalue("isolated_neo4j_settings")
    from core.config import get_config
    from core.graphiti_client import reset_graphiti_client

    for name, value in settings.runtime_environment().items():
        monkeypatch.setenv(name, value)
    get_config.cache_clear()
    reset_graphiti_client()
    try:
        yield settings
    finally:
        reset_graphiti_client()
        get_config.cache_clear()


class _ResultAdapter:
    def __init__(self, records):
        self.records = records


class _DriverAdapter:
    def __init__(self, driver, database):
        self._driver = driver
        self._database = database

    async def execute_query(self, query, **kwargs):
        records, _, _ = await self._driver.execute_query(
            query, parameters_=kwargs, database_=self._database
        )
        return _ResultAdapter(records)


class IsolatedGraph:
    """Graphiti-shaped adapter over an admitted driver, with a guarded reset."""

    def __init__(self, driver, settings):
        self.driver = _DriverAdapter(driver, settings.database)
        self._raw = driver
        self._settings = settings

    async def reset(self):
        await reset_test_graph(self._raw, self._settings)


@pytest_asyncio.fixture
async def isolated_graph(neo4j_isolation):
    """Async driver admitted on its own connection pool; closed after the test."""
    driver = await open_admitted_async_driver(neo4j_isolation)
    try:
        yield IsolatedGraph(driver, neo4j_isolation)
    finally:
        await driver.close()


@pytest_asyncio.fixture
async def graphiti_client(neo4j_isolation):
    """
    Get Graphiti client for integration tests.

    Function-scoped: creates a new client for each test to avoid event loop conflicts.
    This ensures Neo4j async driver is bound to the correct event loop for each test.
    The client is only constructed after neo4j_isolation admitted the database.
    """
    from core.graphiti_client import get_graphiti_client, reset_graphiti_client

    # Create a new client instance (not singleton) for this test
    client = get_graphiti_client(force_new=True)
    graphiti = await client.ensure_ready()

    try:
        yield graphiti
    finally:
        # Cleanup: close Neo4j driver and reset singleton
        try:
            driver = getattr(graphiti, 'driver', None)
            if driver and hasattr(driver, 'close'):
                await driver.close()
        except Exception as e:
            # Log but don't fail on cleanup errors
            import logging
            logging.getLogger(__name__).warning(f"Error closing graphiti driver: {e}")
        finally:
            # Reset singleton so next test gets a fresh client
            reset_graphiti_client()


@pytest_asyncio.fixture
async def clean_test_data(graphiti_client):
    """
    Clean up test data before and after each test.

    Uses a specific test group_id to isolate test data.
    """
    test_group_id = "integration_test"
    driver = graphiti_client.driver

    # Clean before test
    await driver.execute_query(
        "MATCH (n) WHERE n.group_id = $gid DETACH DELETE n",
        gid=test_group_id
    )

    yield test_group_id

    # Clean after test
    await driver.execute_query(
        "MATCH (n) WHERE n.group_id = $gid DETACH DELETE n",
        gid=test_group_id
    )


@pytest_asyncio.fixture
async def memory_ops(graphiti_client):
    """Create MemoryOps instance for testing."""
    from core.memory_ops import MemoryOps

    return MemoryOps(graphiti_client, "test_user")


@pytest_asyncio.fixture
async def llm_client():
    """Get LLM client for testing."""
    from core.llm import get_async_client

    client = get_async_client()
    if not client:
        pytest.skip("OPENAI_API_KEY not set")
    return client
