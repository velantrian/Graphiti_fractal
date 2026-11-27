"""Provider-free contracts for the disposable test database guard.

Live evidence (real Neo4j, decoy database, query audit) is in
tests/isolation/test_decoy_rejection.py via `make test-isolation`.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from tests.support import neo4j_isolation as guard

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "r" * 32


def settings(run_id: str = RUN_ID) -> guard.IsolatedNeo4jSettings:
    return guard.IsolatedNeo4jSettings("bolt://127.0.0.1:1", "neo4j", "pw", "neo4j", run_id)


class Recorder:
    def __init__(self, marker_rows):
        self.marker_rows = marker_rows
        self.calls: list[tuple[str, str, str]] = []  # (mode, database, query)


class FakeSyncSession:
    def __init__(self, recorder, database, mode):
        self.r, self.database, self.mode = recorder, database, mode

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self.r.calls.append((self.mode, self.database, " ".join(query.split())))
        if query == guard.MARKER_QUERY:
            return list(self.r.marker_rows)
        if "count(n) AS existing" in query:
            return [{"run_id": params["run_id"]}]
        return []


class FakeSyncDriver:
    def __init__(self, recorder):
        self.r = recorder

    def session(self, *, database, default_access_mode):
        return FakeSyncSession(self.r, database, default_access_mode)


class FakeAsyncResult:
    def __init__(self, rows):
        self.rows = list(rows)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for row in self.rows:
            yield row


class FakeAsyncSession(FakeSyncSession):
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def run(self, query, **params):
        self.r.calls.append((self.mode, self.database, " ".join(query.split())))
        if query == guard.MARKER_QUERY:
            return FakeAsyncResult(self.r.marker_rows)
        return FakeAsyncResult([{"markers": 1}])


class FakeAsyncDriver(FakeSyncDriver):
    def session(self, *, database, default_access_mode):
        return FakeAsyncSession(self.r, database, default_access_mode)


GOOD_MARKER = [{"run_id": RUN_ID, "purpose": guard.MARKER_PURPOSE}]


def test_runtime_neo4j_variables_are_not_test_configuration():
    env = {"NEO4J_URI": "bolt://prod:7687", "NEO4J_USER": "neo4j", "NEO4J_PASSWORD": "secret"}
    with pytest.raises(guard.IsolationNotConfigured) as exc:
        guard.IsolatedNeo4jSettings.from_env(env)
    assert "secret" not in str(exc.value)


def test_run_id_must_be_a_random_token():
    env = {guard.ENV_URI: "bolt://x", guard.ENV_USER: "u", guard.ENV_PASSWORD: "p", guard.ENV_RUN_ID: "test"}
    with pytest.raises(guard.IsolationRejected, match="random token"):
        guard.IsolatedNeo4jSettings.from_env(env)


def test_password_is_not_in_repr():
    assert "pw" not in repr(settings())


@pytest.mark.parametrize(
    "rows, message",
    [
        ([], "no FractalTestDatabase marker"),
        (GOOD_MARKER * 2, "exactly one"),
        ([{"run_id": RUN_ID, "purpose": "something-else"}], "purpose"),
        ([{"run_id": "x" * 32, "purpose": guard.MARKER_PURPOSE}], "another run"),
    ],
)
def test_admission_rejects_with_a_single_read_only_query(rows, message):
    recorder = Recorder(rows)
    with pytest.raises(guard.IsolationRejected, match=message):
        guard.verify_marker_sync(FakeSyncDriver(recorder), settings())
    assert recorder.calls == [("READ", "neo4j", " ".join(guard.MARKER_QUERY.split()))]


def test_admission_accepts_the_runs_own_marker():
    recorder = Recorder(GOOD_MARKER)
    guard.verify_marker_sync(FakeSyncDriver(recorder), settings())
    assert [mode for mode, _, _ in recorder.calls] == ["READ"]


def test_rejected_reset_never_opens_a_write_session():
    recorder = Recorder([])
    with pytest.raises(guard.IsolationRejected):
        asyncio.run(guard.reset_test_graph(FakeAsyncDriver(recorder), settings()))
    assert [mode for mode, _, _ in recorder.calls] == ["READ"]


def test_reset_rechecks_marker_and_keeps_it():
    recorder = Recorder(GOOD_MARKER)
    asyncio.run(guard.reset_test_graph(FakeAsyncDriver(recorder), settings()))
    assert [mode for mode, _, _ in recorder.calls] == ["READ", "WRITE"]
    reset = recorder.calls[1][2]
    assert "WHERE markers = 1" in reset
    assert "WHERE NOT n:FractalTestDatabase" in reset


def test_provisioning_refuses_non_empty_database_without_writing(monkeypatch):
    recorder = Recorder([])
    monkeypatch.setattr(guard, "describe_contents_sync", lambda driver, database: {
        "nodes": [{"labels": ["Episodic"], "properties": {}}], "relationships": [],
        "constraints": [], "indexes": []})
    with pytest.raises(guard.IsolationRejected, match="non-empty"):
        guard.provision_marker_sync(FakeSyncDriver(recorder), run_id=RUN_ID)
    assert recorder.calls == []


def test_provisioning_marks_only_an_empty_database(monkeypatch):
    recorder = Recorder([])
    monkeypatch.setattr(guard, "describe_contents_sync", lambda driver, database: {
        "nodes": [], "relationships": [], "constraints": [], "indexes": []})
    guard.provision_marker_sync(FakeSyncDriver(recorder), run_id=RUN_ID)
    assert len(recorder.calls) == 1
    mode, _, query = recorder.calls[0]
    assert mode == "WRITE" and "WHERE existing = 0" in query


# --- Graphiti on the admitted connection ---------------------------------------

def _graphiti_neo4j_module():
    from graphiti_core.driver import neo4j_driver

    return neo4j_driver


class _OpenedPools:
    """Stand-in for neo4j.AsyncGraphDatabase that records every pool request."""

    def __init__(self):
        self.calls = []

    def driver(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("graphiti_core opened a pool of its own")


def _construct_graphiti_driver(uri="bolt://127.0.0.1:1", user="neo4j", password="pw"):
    # A worker thread has no running loop, so graphiti_core schedules no schema task.
    module = _graphiti_neo4j_module()
    return asyncio.run(asyncio.to_thread(module.Neo4jDriver, uri, user, password))


def test_graphiti_receives_the_admitted_driver_and_opens_no_pool(monkeypatch):
    module = _graphiti_neo4j_module()
    pools = _OpenedPools()
    monkeypatch.setattr(module, "AsyncGraphDatabase", pools)
    admitted = object()
    with guard.graphiti_bound_to_admitted_driver(admitted, settings()) as handed_out:
        graph_driver = _construct_graphiti_driver()
    assert graph_driver.client.wrapped is admitted
    assert handed_out == [admitted]
    assert pools.calls == []
    assert module.AsyncGraphDatabase is pools  # restored after the context


@pytest.mark.parametrize(
    "uri, user, password",
    [("bolt://127.0.0.1:2", "neo4j", "pw"), ("bolt://127.0.0.1:1", "other", "pw"),
     ("bolt://127.0.0.1:1", "neo4j", "other")],
)
def test_graphiti_cannot_reach_a_connection_other_than_the_admitted_one(monkeypatch, uri, user, password):
    module = _graphiti_neo4j_module()
    pools = _OpenedPools()
    monkeypatch.setattr(module, "AsyncGraphDatabase", pools)
    with guard.graphiti_bound_to_admitted_driver(object(), settings()) as handed_out:
        with pytest.raises(guard.IsolationRejected, match="different Neo4j connection"):
            _construct_graphiti_driver(uri, user, password)
    assert handed_out == [] and pools.calls == []


def test_graphiti_cannot_open_a_second_driver(monkeypatch):
    module = _graphiti_neo4j_module()
    monkeypatch.setattr(module, "AsyncGraphDatabase", _OpenedPools())
    with guard.graphiti_bound_to_admitted_driver(object(), settings()):
        _construct_graphiti_driver()
        with pytest.raises(guard.IsolationRejected, match="second Neo4j driver"):
            _construct_graphiti_driver()


def test_graphiti_binding_refuses_a_database_graphiti_would_not_use(monkeypatch):
    module = _graphiti_neo4j_module()
    pools = _OpenedPools()
    monkeypatch.setattr(module, "AsyncGraphDatabase", pools)
    other_db = guard.IsolatedNeo4jSettings("bolt://127.0.0.1:1", "neo4j", "pw", "scratch", RUN_ID)
    with pytest.raises(guard.IsolationRejected, match="unverified database"):
        with guard.graphiti_bound_to_admitted_driver(object(), other_db):
            raise AssertionError("must be rejected before the body runs")
    assert module.AsyncGraphDatabase is pools and pools.calls == []


def test_graphiti_binding_is_undone_when_the_body_fails(monkeypatch):
    module = _graphiti_neo4j_module()
    pools = _OpenedPools()
    monkeypatch.setattr(module, "AsyncGraphDatabase", pools)
    with pytest.raises(RuntimeError, match="boom"):
        with guard.graphiti_bound_to_admitted_driver(object(), settings()):
            raise RuntimeError("boom")
    assert module.AsyncGraphDatabase is pools


def test_provider_e2e_is_admitted_before_any_client_is_built():
    source = (ROOT / "tests/e2e/test_provider_backed_memory_e2e.py").read_text(encoding="utf-8")
    conftest = (ROOT / "tests/e2e/conftest.py").read_text(encoding="utf-8")
    assert "open_admitted_async_driver" in conftest
    assert "def test_provider_backed_ingest_and_recall_roundtrip(admitted_neo4j" in source
    assert "def test_admission_hands_graphiti_the_admitted_connection(admitted_neo4j" in source
    # the only client construction happens inside the binding to the admitted driver
    assert source.count("get_graphiti_client(") == 1
    binding = source.index("with graphiti_bound_to_admitted_driver(admitted_neo4j.driver, settings):\n"
                           "        client = get_graphiti_client(force_new=True)")
    assert binding > source.index("def test_provider_backed_ingest_and_recall_roundtrip")
    assert "skipif" not in source  # protection must not depend on how pytest is invoked


# --- static guards over test sources -----------------------------------------

UNSCOPED_WIPE = re.compile(r"MATCH\s*\(\s*\w+\s*\)\s*DETACH\s+DELETE", re.IGNORECASE)
RUNTIME_ENV_READ = re.compile(r"""os\.(?:environ\.get|getenv|environ\[)\(?\s*["']NEO4J_""")


def _database_test_sources():
    for folder in ("tests/integration", "tests/isolation", "tests/support", "tests/e2e"):
        yield from sorted((ROOT / folder).glob("*.py"))


def test_database_tests_have_no_unguarded_full_graph_wipe():
    offenders = [
        str(path.relative_to(ROOT))
        for path in _database_test_sources()
        if path.name != "neo4j_isolation.py" and UNSCOPED_WIPE.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], "use IsolatedGraph.reset() / reset_test_graph() instead"


def test_database_tests_never_read_runtime_neo4j_settings():
    offenders = [
        str(path.relative_to(ROOT))
        for path in _database_test_sources()
        if RUNTIME_ENV_READ.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_disposable_image_matches_reviewed_compose_pin():
    script = (ROOT / "scripts/test_neo4j.py").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    pinned = re.search(r'DEFAULT_IMAGE = "([^"]+)"', script).group(1)
    assert f"image: {pinned}" in compose


# --- every statement of the Graphiti client is pinned to the database whose marker was verified ----------

class _RecordingAsyncDriver:
    """Stand-in for the admitted neo4j.AsyncDriver: records the database-selection argument of every call."""

    def __init__(self):
        self.queries, self.sessions = [], []

    async def execute_query(self, query_, parameters_=None, routing_=None, database_=None, **kwargs):
        self.queries.append({"query": query_, "database_": database_, "parameters_": dict(parameters_ or {}),
                             "kwargs": kwargs})
        return "result"

    def session(self, database=None, **kwargs):
        self.sessions.append({"database": database, **kwargs})
        return object()

    async def verify_connectivity(self):
        return None

    async def close(self):
        return None


def _bound_graphiti_driver(recorder, db="neo4j"):
    s = guard.IsolatedNeo4jSettings("bolt://127.0.0.1:1", "neo4j", "pw", db, RUN_ID)
    with guard.graphiti_bound_to_admitted_driver(recorder, s):
        return _construct_graphiti_driver()


def test_real_neo4j_driver_passes_the_admitted_database_as_the_selection_argument(monkeypatch):
    """The real graphiti_core Neo4jDriver, a recording driver underneath: ``database_`` must be the
    selection argument of execute_query (graphiti_core itself only puts it into the *parameters*)."""
    recorder = _RecordingAsyncDriver()
    graph_driver = _bound_graphiti_driver(recorder)
    asyncio.run(graph_driver.execute_query("RETURN 1 AS one"))
    asyncio.run(graph_driver.execute_query("MATCH (n) RETURN count(n) AS c", n=1))
    asyncio.run(graph_driver.delete_all_indexes())            # graphiti_core sends this without any database
    assert len(recorder.queries) == 3
    assert [q["database_"] for q in recorder.queries] == ["neo4j"] * 3
    assert all("database_" not in q["parameters_"] for q in recorder.queries), \
        "the database name must not travel as a query parameter"


def test_sessions_and_transactions_of_the_real_driver_are_pinned_too():
    recorder = _RecordingAsyncDriver()
    graph_driver = _bound_graphiti_driver(recorder)
    graph_driver.session()
    graph_driver.session("neo4j")
    assert [s["database"] for s in recorder.sessions] == ["neo4j", "neo4j"]


@pytest.mark.parametrize("other", ["system", "scratch", "production"])
def test_selecting_another_database_is_rejected_before_it_reaches_the_driver(other):
    recorder = _RecordingAsyncDriver()
    graph_driver = _bound_graphiti_driver(recorder)
    with pytest.raises(guard.IsolationRejected, match="not the admitted database"):
        graph_driver.session(other)
    with pytest.raises(guard.IsolationRejected, match="not the admitted database"):
        asyncio.run(graph_driver.with_database(other).execute_query("RETURN 1"))            # shared pool, other default
    with pytest.raises(guard.IsolationRejected, match="not the admitted database"):
        asyncio.run(graph_driver.execute_query("RETURN 1", params={"database_": other}))    # per-call override
    with pytest.raises(guard.IsolationRejected, match="not the admitted database"):
        asyncio.run(graph_driver.client.execute_query("RETURN 1", database_=other))         # the raw client
    assert recorder.queries == [] and recorder.sessions == []


def test_the_pinned_view_has_no_way_to_use_another_database_through_other_apis():
    recorder = _RecordingAsyncDriver()
    graph_driver = _bound_graphiti_driver(recorder)
    for call in (lambda: graph_driver.client.session(database="system"),
                 lambda: graph_driver.client.session(database="system", default_access_mode="r"),
                 lambda: graph_driver.client.execute_query("RETURN 1", impersonated_user_="admin")):
        with pytest.raises(guard.IsolationRejected):
            result = call()
            if asyncio.iscoroutine(result):
                asyncio.run(result)
    assert recorder.queries == [] and recorder.sessions == []
