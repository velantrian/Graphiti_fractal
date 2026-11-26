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


# --- static guards over test sources -----------------------------------------

UNSCOPED_WIPE = re.compile(r"MATCH\s*\(\s*\w+\s*\)\s*DETACH\s+DELETE", re.IGNORECASE)
RUNTIME_ENV_READ = re.compile(r"""os\.(?:environ\.get|getenv|environ\[)\(?\s*["']NEO4J_""")


def _database_test_sources():
    for folder in ("tests/integration", "tests/isolation", "tests/support"):
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
