"""Acceptance: a misconfigured test run is rejected before its first mutation.

Run through ``python scripts/test_neo4j.py acceptance`` (``make test-isolation``),
which provides two disposable databases:

* allowed (FRACTAL_TEST_*): provisioned with this run's marker;
* decoy (FRACTAL_DECOY_*): a synthetic control record, constraint and episode
  but no marker. It stands in for a developer's real database.

Each scenario runs the integration suite in a subprocess with the
``tests.support.neo4j_query_audit`` plugin, which records every statement the
process sends. Evidence per scenario: the audit log (attempts), a read-only
inventory of the target database (content and schema), and, when the
container name is known, a checksum of its transaction log (committed writes).
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from neo4j import GraphDatabase

from tests.support.neo4j_isolation import (
    MARKER_QUERY,
    IsolatedNeo4jSettings,
    IsolationRejected,
    describe_contents_sync,
    open_admitted_async_driver,
    provision_marker_sync,
    reset_test_graph,
)

ROOT = Path(__file__).resolve().parents[2]
DECOY_KEYS = ("FRACTAL_DECOY_NEO4J_URI", "FRACTAL_DECOY_NEO4J_USER", "FRACTAL_DECOY_NEO4J_PASSWORD")
ALLOWED_KEYS = ("FRACTAL_TEST_NEO4J_URI", "FRACTAL_TEST_NEO4J_USER", "FRACTAL_TEST_NEO4J_PASSWORD",
                "FRACTAL_TEST_RUN_ID")

pytestmark = pytest.mark.skipif(
    any(not os.environ.get(key) for key in DECOY_KEYS + ALLOWED_KEYS),
    reason="needs allowed and decoy databases; run `python scripts/test_neo4j.py acceptance`",
)

TARGETS = (os.environ.get("FRACTAL_ISOLATION_TARGETS") or "tests/integration").split()
STRIPPED_PREFIXES = ("FRACTAL_TEST_", "FRACTAL_DECOY_", "FRACTAL_REQUIRE_TEST_NEO4J", "NEO4J_")


def _decoy() -> dict[str, str]:
    return {
        "uri": os.environ["FRACTAL_DECOY_NEO4J_URI"],
        "user": os.environ["FRACTAL_DECOY_NEO4J_USER"],
        "password": os.environ["FRACTAL_DECOY_NEO4J_PASSWORD"],
        "container": os.environ.get("FRACTAL_DECOY_CONTAINER", ""),
    }


def _allowed() -> dict[str, str]:
    return {
        "uri": os.environ["FRACTAL_TEST_NEO4J_URI"],
        "user": os.environ["FRACTAL_TEST_NEO4J_USER"],
        "password": os.environ["FRACTAL_TEST_NEO4J_PASSWORD"],
        "container": os.environ.get("FRACTAL_TEST_CONTAINER", ""),
    }


def _txlog(container: str) -> str | None:
    if not container:
        return None
    return subprocess.run(
        ["docker", "exec", container, "sh", "-c", "md5sum /data/transactions/neo4j/neostore.transaction.db.*"],
        check=True, capture_output=True, text=True,
    ).stdout


def _state(target: dict[str, str]) -> dict:
    driver = GraphDatabase.driver(target["uri"], auth=(target["user"], target["password"]))
    try:
        contents = describe_contents_sync(driver)
    finally:
        driver.close()
    return {"contents": contents, "txlog": _txlog(target["container"])}


def _run_suite(tmp_path: Path, overrides: dict[str, str]):
    tmp_path.mkdir(parents=True, exist_ok=True)
    audit = tmp_path / "audit.jsonl"
    junit = tmp_path / "junit.xml"
    env = {k: v for k, v in os.environ.items() if not k.startswith(STRIPPED_PREFIXES)}
    env.update(overrides)
    env["FRACTAL_TEST_NEO4J_QUERY_AUDIT"] = str(audit)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "tests.support.neo4j_query_audit",
         "-p", "no:cacheprovider", f"--junitxml={junit}", *TARGETS],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=900,
    )
    events = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert events and events[0]["event"] == "audit-installed", "query audit was not active"
    cases = []
    for case in ET.parse(junit).getroot().iter("testcase"):
        outcome = "passed"
        message = ""
        for child in case:
            if child.tag in {"error", "failure", "skipped"}:
                outcome, message = child.tag, (child.get("message") or "") + (child.text or "")
        cases.append({"name": f"{case.get('classname')}::{case.get('name')}", "outcome": outcome,
                      "message": message})
    return proc.returncode, proc.stdout + proc.stderr, events, cases


def _neo4j_cases(cases):
    # test_embedding_cache is explicitly marked no_neo4j (local cache only).
    return [case for case in cases if "test_embedding_cache" not in case["name"]]


def _only_read_marker_queries(events) -> list[dict]:
    queries = [event for event in events if event["event"] == "query"]
    for query in queries:
        assert query["mode"] == "READ", query
        assert query["query"] == " ".join(MARKER_QUERY.split()), query
    return queries


def test_unmarked_database_is_rejected_before_any_write(tmp_path):
    decoy = _decoy()
    before = _state(decoy)
    overrides = {
        "FRACTAL_TEST_NEO4J_URI": decoy["uri"],
        "FRACTAL_TEST_NEO4J_USER": decoy["user"],
        "FRACTAL_TEST_NEO4J_PASSWORD": decoy["password"],
        "FRACTAL_TEST_RUN_ID": secrets.token_urlsafe(24),
        "FRACTAL_REQUIRE_TEST_NEO4J": "1",
        # A developer shell that also exports the runtime database.
        "NEO4J_URI": decoy["uri"],
        "NEO4J_USER": decoy["user"],
        "NEO4J_PASSWORD": decoy["password"],
    }
    code, output, events, cases = _run_suite(tmp_path, overrides)
    after = _state(decoy)

    assert code != 0, output
    neo4j_cases = _neo4j_cases(cases)
    assert neo4j_cases
    assert not [case for case in neo4j_cases if case["outcome"] in {"passed", "failure"}], neo4j_cases
    assert any("isolation guard rejected test database" in case["message"] for case in neo4j_cases)
    queries = _only_read_marker_queries(events)
    assert len(queries) == 1, queries  # one read-only admission per session, nothing else
    if decoy["container"]:
        assert before["txlog"], "transaction log fingerprint unavailable"
    assert after == before  # content, schema and (when known) committed transaction log
    assert {"labels": ["IsolationControl"],
            "properties": {"id": "decoy-control",
                           "note": "synthetic control record; must survive"}} in after["contents"]["nodes"]


def test_runtime_neo4j_variables_are_never_a_fallback(tmp_path):
    decoy = _decoy()
    before = _state(decoy)
    runtime_only = {
        "NEO4J_URI": decoy["uri"],
        "NEO4J_USER": decoy["user"],
        "NEO4J_PASSWORD": decoy["password"],
    }
    code, output, events, cases = _run_suite(tmp_path / "local", runtime_only)
    assert code == 0, output
    assert all(case["outcome"] == "skipped" for case in _neo4j_cases(cases)), cases
    assert [event for event in events if event["event"] in {"driver", "query"}] == []

    required = dict(runtime_only, FRACTAL_REQUIRE_TEST_NEO4J="1")
    code, output, events, cases = _run_suite(tmp_path / "ci", required)
    assert code != 0, output
    assert not [case for case in _neo4j_cases(cases) if case["outcome"] in {"passed", "failure"}]
    assert [event for event in events if event["event"] in {"driver", "query"}] == []
    assert _state(decoy) == before


def test_marker_of_another_run_is_rejected(tmp_path):
    allowed = _allowed()
    before = _state(allowed)
    overrides = {
        "FRACTAL_TEST_NEO4J_URI": allowed["uri"],
        "FRACTAL_TEST_NEO4J_USER": allowed["user"],
        "FRACTAL_TEST_NEO4J_PASSWORD": allowed["password"],
        "FRACTAL_TEST_RUN_ID": secrets.token_urlsafe(24),
        "FRACTAL_REQUIRE_TEST_NEO4J": "1",
    }
    code, output, events, cases = _run_suite(tmp_path, overrides)
    assert code != 0, output
    assert any("marker belongs to another run" in case["message"] for case in _neo4j_cases(cases))
    assert len(_only_read_marker_queries(events)) == 1
    assert _state(allowed) == before


def test_provisioning_refuses_a_database_that_holds_data():
    decoy = _decoy()
    before = _state(decoy)
    driver = GraphDatabase.driver(decoy["uri"], auth=(decoy["user"], decoy["password"]))
    try:
        with pytest.raises(IsolationRejected, match="non-empty database"):
            provision_marker_sync(driver, run_id=secrets.token_urlsafe(24))
    finally:
        driver.close()
    assert _state(decoy) == before


@pytest.mark.asyncio
async def test_guarded_reset_keeps_the_marker_and_rechecks_it():
    settings = IsolatedNeo4jSettings.from_env()
    driver = await open_admitted_async_driver(settings)
    try:
        await driver.execute_query("CREATE (:ResetProbe {n:1})-[:R]->(:ResetProbe {n:2})")
        await reset_test_graph(driver, settings)
        records, _, _ = await driver.execute_query(
            "MATCH (n) RETURN labels(n) AS labels, n.run_id AS run_id"
        )
        assert [(r["labels"], r["run_id"]) for r in records] == [(["FractalTestDatabase"], settings.run_id)]
        await reset_test_graph(driver, settings)  # proof survives repeated resets

        stale = IsolatedNeo4jSettings(settings.uri, settings.user, settings.password, settings.database,
                                      secrets.token_urlsafe(24))
        await driver.execute_query("CREATE (:ResetProbe {n:3})")
        with pytest.raises(IsolationRejected, match="another run"):
            await reset_test_graph(driver, stale)
        records, _, _ = await driver.execute_query("MATCH (p:ResetProbe) RETURN count(p) AS c")
        assert records[0]["c"] == 1  # rejected reset deleted nothing
    finally:
        await reset_test_graph(driver, settings)
        await driver.close()
