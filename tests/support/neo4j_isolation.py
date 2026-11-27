"""Admission guard for disposable Neo4j test databases.

A test process may mutate a Neo4j database only after it has proven, with a
read-only query, that the database is the disposable resource provisioned for
*this* run:

* Test configuration comes only from ``FRACTAL_TEST_NEO4J_*`` and
  ``FRACTAL_TEST_RUN_ID``. Runtime ``NEO4J_*`` variables and ``.env`` are never
  used as a fallback.
* Test infrastructure (``scripts/test_neo4j.py``) creates exactly one
  ``(:FractalTestDatabase {run_id, purpose})`` marker, and only in a database
  that is empty (no nodes, no constraints, no non-lookup indexes). A database
  that already holds data can therefore never be marked by the tooling.
* Test code only *reads* the marker, in a READ_ACCESS session, which Neo4j
  enforces server-side (``Neo.ClientError.Statement.AccessMode``).
* Full-graph cleanup re-verifies the marker and keeps the marker node, so the
  proof survives every reset and every ``finally`` block.

Admission must happen before a Graphiti client is constructed: graphiti_core's
``Neo4jDriver.__init__`` schedules ``build_indices_and_constraints()`` as soon
as it is created inside a running event loop. ``graphiti_bound_to_admitted_driver``
makes that constructor reuse the admitted driver instead of opening its own, so
admission, schema setup and writes share one connection pool.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping

from neo4j import READ_ACCESS, WRITE_ACCESS

MARKER_LABEL = "FractalTestDatabase"
MARKER_PURPOSE = "disposable-integration-test"

ENV_URI = "FRACTAL_TEST_NEO4J_URI"
ENV_USER = "FRACTAL_TEST_NEO4J_USER"
ENV_PASSWORD = "FRACTAL_TEST_NEO4J_PASSWORD"
ENV_DATABASE = "FRACTAL_TEST_NEO4J_DATABASE"
ENV_RUN_ID = "FRACTAL_TEST_RUN_ID"
# Set by CI and scripts/test_neo4j.py: a missing test database is an error,
# not a silent skip.
ENV_REQUIRE = "FRACTAL_REQUIRE_TEST_NEO4J"

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{24,128}$")

MARKER_QUERY = (
    f"MATCH (m:{MARKER_LABEL}) "
    "RETURN m.run_id AS run_id, m.purpose AS purpose"
)

# Executed only after a successful read-only admission. The marker condition is
# repeated inside the write transaction, and the marker itself is never deleted.
RESET_QUERY = f"""
MATCH (m:{MARKER_LABEL} {{run_id:$run_id, purpose:$purpose}})
WITH count(m) AS markers
WHERE markers = 1
CALL () {{
    MATCH (n) WHERE NOT n:{MARKER_LABEL}
    DETACH DELETE n
}}
RETURN markers
"""

PROVISION_QUERY = f"""
CALL () {{ MATCH (n) RETURN count(n) AS existing }}
WITH existing
WHERE existing = 0
CREATE (m:{MARKER_LABEL} {{
    run_id:$run_id,
    purpose:$purpose,
    created_at:datetime(),
    provisioned_by:'scripts/test_neo4j.py'
}})
RETURN m.run_id AS run_id
"""


class IsolationNotConfigured(RuntimeError):
    """No disposable test database was configured for this process."""


class IsolationRejected(RuntimeError):
    """The configured database is not the disposable resource of this run."""


@dataclass(frozen=True)
class IsolatedNeo4jSettings:
    uri: str
    user: str
    password: str = field(repr=False)
    database: str
    run_id: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "IsolatedNeo4jSettings":
        env = os.environ if environ is None else environ
        required = (ENV_URI, ENV_USER, ENV_PASSWORD, ENV_RUN_ID)
        missing = [name for name in required if not env.get(name)]
        if missing:
            raise IsolationNotConfigured(
                "isolated test Neo4j is not configured (missing "
                + ", ".join(missing)
                + "); runtime NEO4J_* settings are never used by tests. "
                "Run `make test-integration` to start a disposable database."
            )
        run_id = env[ENV_RUN_ID]
        if not RUN_ID_PATTERN.match(run_id):
            raise IsolationRejected(
                f"{ENV_RUN_ID} must be a random token of 24-128 [A-Za-z0-9_-] characters"
            )
        return cls(
            uri=env[ENV_URI],
            user=env[ENV_USER],
            password=env[ENV_PASSWORD],
            database=env.get(ENV_DATABASE) or "neo4j",
            run_id=run_id,
        )

    def runtime_environment(self) -> dict[str, str]:
        """NEO4J_* values for application code, valid only after admission."""
        return {
            "NEO4J_URI": self.uri,
            "NEO4J_USER": self.user,
            "NEO4J_PASSWORD": self.password,
            "NEO4J_DATABASE": self.database,
        }


def require_test_database() -> bool:
    return os.environ.get(ENV_REQUIRE) == "1"


def _field(record: Any, key: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(key)
    return record[key]


def check_marker_records(records: Iterable[Any], settings: IsolatedNeo4jSettings) -> None:
    rows = list(records)
    if not rows:
        raise IsolationRejected(
            f"no {MARKER_LABEL} marker in database {settings.database!r} at {settings.uri}; "
            "refusing to use a database that test infrastructure did not provision"
        )
    if len(rows) != 1:
        raise IsolationRejected(
            f"expected exactly one {MARKER_LABEL} marker, found {len(rows)}; refusing ambiguous database"
        )
    run_id = _field(rows[0], "run_id")
    purpose = _field(rows[0], "purpose")
    if purpose != MARKER_PURPOSE:
        raise IsolationRejected(f"marker purpose {purpose!r} is not {MARKER_PURPOSE!r}")
    if run_id != settings.run_id:
        raise IsolationRejected(
            f"marker belongs to another run; {ENV_RUN_ID} does not match the database marker"
        )


# --- read-only admission -----------------------------------------------------

def verify_marker_sync(driver: Any, settings: IsolatedNeo4jSettings) -> None:
    with driver.session(database=settings.database, default_access_mode=READ_ACCESS) as session:
        records = list(session.run(MARKER_QUERY))
    check_marker_records(records, settings)


async def verify_marker_async(driver: Any, settings: IsolatedNeo4jSettings) -> None:
    async with driver.session(database=settings.database, default_access_mode=READ_ACCESS) as session:
        result = await session.run(MARKER_QUERY)
        records = [record async for record in result]
    check_marker_records(records, settings)


def wait_until_reachable_sync(driver: Any, uri: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            driver.verify_connectivity()
            return
        except Exception as exc:  # noqa: BLE001 - readiness probe only, no writes
            if time.monotonic() >= deadline:
                raise IsolationRejected(f"test Neo4j at {uri} not reachable: {type(exc).__name__}")
            time.sleep(1)


def admit_sync(settings: IsolatedNeo4jSettings, *, timeout: float = 60.0) -> None:
    """Open a throwaway sync driver, wait for readiness, verify the marker, close."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(settings.uri, auth=(settings.user, settings.password))
    try:
        wait_until_reachable_sync(driver, settings.uri, timeout)
        verify_marker_sync(driver, settings)
    finally:
        driver.close()


async def open_admitted_async_driver(settings: IsolatedNeo4jSettings, *, timeout: float = 60.0):
    """Return an AsyncDriver whose own connection pool has passed admission."""
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(settings.uri, auth=(settings.user, settings.password))
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                await driver.verify_connectivity()
                break
            except Exception as exc:  # noqa: BLE001
                if time.monotonic() >= deadline:
                    raise IsolationRejected(
                        f"test Neo4j at {settings.uri} not reachable: {type(exc).__name__}"
                    )
                await asyncio.sleep(1)
        await verify_marker_async(driver, settings)
    except BaseException:
        await driver.close()
        raise
    return driver


# --- application client on the admitted connection ---------------------------

_UNSET = object()


class DatabasePinnedDriver:
    """The admitted AsyncDriver seen through one database: the one whose marker was verified.

    graphiti_core's ``Neo4jDriver`` does not select a database with the driver's own
    ``database_`` argument: it puts ``database_`` into the query *parameters*, so such a
    statement runs on the user's home database; ``delete_all_indexes()`` names no database at
    all; ``with_database()`` / ``session(database)`` / a ``params={'database_': ...}`` override
    can name any other. Every one of those ends here: each statement and session is sent with
    ``database_`` / ``database`` set to the admitted database as the selection argument, and any
    attempt to name another database (or to impersonate a user) is rejected before it reaches
    the driver. Only the calls graphiti_core makes exist on this view.
    """

    def __init__(self, driver: Any, database: str):
        self._driver = driver
        self._database = database

    @property
    def wrapped(self) -> Any:
        return self._driver

    def _only_admitted(self, name: Any) -> None:
        if name is not None and name != self._database:
            raise IsolationRejected(
                f"database {name!r} is not the admitted database {self._database!r}; refusing"
            )

    async def execute_query(self, query_: Any, parameters_: Mapping[str, Any] | None = None,
                            routing_: Any = _UNSET, database_: Any = None,
                            impersonated_user_: Any = None, **kwargs: Any) -> Any:
        self._only_admitted(database_)
        if impersonated_user_ is not None:
            raise IsolationRejected("impersonation is not allowed on the admitted connection")
        parameters = dict(parameters_ or {})
        # graphiti_core's way of "choosing" a database: only a parameter. Check it, never forward it.
        self._only_admitted(parameters.pop("database_", None))
        extra = {} if routing_ is _UNSET else {"routing_": routing_}
        return await self._driver.execute_query(
            query_, parameters_=parameters, database_=self._database, **extra, **kwargs
        )

    def session(self, database: str | None = None, **kwargs: Any) -> Any:
        self._only_admitted(database)
        if kwargs.get("impersonated_user") is not None:
            raise IsolationRejected("impersonation is not allowed on the admitted connection")
        return self._driver.session(database=self._database, **kwargs)

    async def verify_connectivity(self) -> Any:
        return await self._driver.verify_connectivity()

    async def close(self) -> None:
        await self._driver.close()

def graphiti_default_database() -> str:
    """Database that ``Graphiti(uri=...)`` uses: the default of ``Neo4jDriver``."""
    from graphiti_core.driver.neo4j_driver import Neo4jDriver

    return inspect.signature(Neo4jDriver.__init__).parameters["database"].default


@contextlib.contextmanager
def graphiti_bound_to_admitted_driver(driver: Any, settings: IsolatedNeo4jSettings) -> Iterator[list]:
    """Hand graphiti_core the admitted AsyncDriver instead of a pool of its own.

    graphiti_core's ``Neo4jDriver.__init__`` opens its own pool with
    ``AsyncGraphDatabase.driver(...)`` and, inside an event loop, immediately
    schedules schema writes on it. While this context is active that call
    returns *driver* itself, so the connection whose marker was verified is the
    one Graphiti uses for schema setup and every later write. A request for a
    different URI or credentials, or for a second driver, is rejected before any
    pool exists. A database other than the one ``Graphiti(uri=...)`` always uses
    is rejected before the constructor can run.

    Yields the list of drivers handed out (at most one).
    """
    from graphiti_core.driver import neo4j_driver as graphiti_neo4j

    expected = graphiti_default_database()
    if settings.database != expected:
        raise IsolationRejected(
            f"Graphiti(uri=...) writes to database {expected!r}, but database "
            f"{settings.database!r} was admitted; refusing to let it use an unverified database"
        )
    handed_out: list = []
    original = graphiti_neo4j.AsyncGraphDatabase

    class _AdmittedDriverFactory:
        @staticmethod
        def driver(uri: Any = None, auth: Any = None, **kwargs: Any) -> Any:
            if handed_out:
                raise IsolationRejected("Graphiti asked for a second Neo4j driver; only the admitted one exists")
            if kwargs or uri != settings.uri or tuple(auth or ()) != (settings.user, settings.password):
                raise IsolationRejected(
                    "Graphiti asked for a different Neo4j connection than the admitted one; refusing"
                )
            handed_out.append(driver)
            return DatabasePinnedDriver(driver, settings.database)

    graphiti_neo4j.AsyncGraphDatabase = _AdmittedDriverFactory
    try:
        yield handed_out
    finally:
        graphiti_neo4j.AsyncGraphDatabase = original


# --- guarded mutation --------------------------------------------------------

async def reset_test_graph(driver: Any, settings: IsolatedNeo4jSettings) -> None:
    """Delete every node except the marker, after re-verifying admission."""
    await verify_marker_async(driver, settings)
    async with driver.session(database=settings.database, default_access_mode=WRITE_ACCESS) as session:
        result = await session.run(RESET_QUERY, run_id=settings.run_id, purpose=MARKER_PURPOSE)
        records = [record async for record in result]
    if len(records) != 1:
        raise IsolationRejected("marker disappeared before reset; nothing was deleted")


# --- infrastructure only -----------------------------------------------------

def describe_contents_sync(driver: Any, database: str = "neo4j") -> dict[str, Any]:
    """Read-only inventory used for provisioning and acceptance snapshots."""
    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        nodes = [
            {"labels": sorted(r["labels"]), "properties": _jsonable(r["properties"])}
            for r in session.run("MATCH (n) RETURN labels(n) AS labels, properties(n) AS properties")
        ]
        rels = [
            {"type": r["type"], "properties": _jsonable(r["properties"])}
            for r in session.run("MATCH ()-[r]->() RETURN type(r) AS type, properties(r) AS properties")
        ]
        constraints = sorted(r["name"] for r in session.run("SHOW CONSTRAINTS YIELD name RETURN name"))
        indexes = sorted(
            r["name"]
            for r in session.run("SHOW INDEXES YIELD name, type WHERE type <> 'LOOKUP' RETURN name")
        )
    nodes.sort(key=repr)
    rels.sort(key=repr)
    return {"nodes": nodes, "relationships": rels, "constraints": constraints, "indexes": indexes}


def provision_marker_sync(driver: Any, *, run_id: str, database: str = "neo4j") -> None:
    """Mark a *fresh, empty* database as this run's disposable test resource."""
    if not RUN_ID_PATTERN.match(run_id):
        raise IsolationRejected("run id must be a random token of 24-128 characters")
    contents = describe_contents_sync(driver, database)
    if contents["nodes"] or contents["relationships"] or contents["constraints"] or contents["indexes"]:
        raise IsolationRejected(
            "refusing to provision a non-empty database "
            f"({len(contents['nodes'])} nodes, {len(contents['constraints'])} constraints, "
            f"{len(contents['indexes'])} indexes); only a fresh disposable database can be marked"
        )
    with driver.session(database=database, default_access_mode=WRITE_ACCESS) as session:
        records = list(session.run(PROVISION_QUERY, run_id=run_id, purpose=MARKER_PURPOSE))
    if len(records) != 1:
        raise IsolationRejected("database stopped being empty during provisioning; no marker created")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
