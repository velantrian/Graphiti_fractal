#!/usr/bin/env python
"""Disposable, marker-verified Neo4j for integration tests (local runs and CI).

  python scripts/test_neo4j.py run -- python -m pytest -q tests/integration
  python scripts/test_neo4j.py acceptance [--target PATH ...]
  python scripts/test_neo4j.py provision [--run-id ID]

``run`` starts a fresh Neo4j container on a random localhost port with a random
password, marks it for this run (the database must be empty) and runs the
command with only FRACTAL_TEST_* settings; runtime NEO4J_* variables are
removed from the child environment. The container is removed afterwards.

``acceptance`` additionally starts a decoy database holding a synthetic control
record and runs tests/isolation/test_decoy_rejection.py to show that a wrong
configuration is rejected before its first mutation.

``provision`` marks a fresh, empty database supplied by other infrastructure
(read from FRACTAL_TEST_NEO4J_URI/USER/PASSWORD) and prints the run id.

See docs/TEST_DATABASE_ISOLATION.md.
"""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo4j import GraphDatabase  # noqa: E402

from tests.support.neo4j_isolation import (  # noqa: E402
    ENV_DATABASE,
    ENV_PASSWORD,
    ENV_REQUIRE,
    ENV_RUN_ID,
    ENV_URI,
    ENV_USER,
    IsolationRejected,
    provision_marker_sync,
    wait_until_reachable_sync,
)

DEFAULT_IMAGE = "neo4j:5.26.29-community"
RUNTIME_VARIABLES = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "NEO4J_DATABASE")
DEFAULT_ACCEPTANCE_TARGETS = ("tests/integration/test_neo4j_operational_contracts.py",)


@dataclass
class Container:
    name: str
    uri: str
    password: str = field(repr=False)
    user: str = "neo4j"


def new_run_id() -> str:
    return secrets.token_urlsafe(24)


def image() -> str:
    return os.environ.get("FRACTAL_TEST_NEO4J_IMAGE") or DEFAULT_IMAGE


def start_container(kind: str) -> Container:
    name = f"fractal-test-{kind}-{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(24)
    # The password reaches docker through the environment, not argv.
    env = dict(os.environ, NEO4J_AUTH=f"neo4j/{password}")
    subprocess.run(
        [
            "docker", "run", "--detach", "--rm", "--name", name,
            "--label", "org.fractal.test=disposable",
            "--publish", "127.0.0.1::7687",
            "--env", "NEO4J_AUTH",
            "--env", "NEO4J_server_memory_heap_max__size=512m",
            "--env", "NEO4J_server_memory_pagecache_size=128m",
            image(),
        ],
        check=True, env=env, stdout=subprocess.DEVNULL,
    )
    try:
        mapping = subprocess.run(
            ["docker", "port", name, "7687/tcp"], check=True, capture_output=True, text=True,
        ).stdout.split()[0]
        container = Container(name=name, uri=f"bolt://{mapping}", password=password)
        driver = GraphDatabase.driver(container.uri, auth=(container.user, container.password))
        try:
            wait_until_reachable_sync(driver, container.uri, timeout=180)
        finally:
            driver.close()
        return container
    except BaseException:
        stop_container(name)
        raise


def stop_container(name: str) -> None:
    subprocess.run(["docker", "rm", "--force", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def provision(container: Container, run_id: str) -> None:
    driver = GraphDatabase.driver(container.uri, auth=(container.user, container.password))
    try:
        provision_marker_sync(driver, run_id=run_id)
    finally:
        driver.close()


def seed_decoy(container: Container) -> None:
    """Synthetic stand-in for a developer's real database: data plus schema."""
    driver = GraphDatabase.driver(container.uri, auth=(container.user, container.password))
    try:
        driver.execute_query(
            "CREATE CONSTRAINT isolation_control_id IF NOT EXISTS "
            "FOR (c:IsolationControl) REQUIRE c.id IS UNIQUE"
        )
        driver.execute_query(
            "CREATE (:IsolationControl {id:'decoy-control', note:'synthetic control record; must survive'})"
            "-[:CONTROL_LINK]->(:Episodic {uuid:'decoy-episode', group_id:'personal', content:'synthetic'})"
        )
        driver.execute_query("CALL db.awaitIndexes(120)")
    finally:
        driver.close()


def test_environment(container: Container, run_id: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in RUNTIME_VARIABLES}
    env.update({
        ENV_URI: container.uri,
        ENV_USER: container.user,
        ENV_PASSWORD: container.password,
        ENV_DATABASE: "neo4j",
        ENV_RUN_ID: run_id,
        ENV_REQUIRE: "1",
    })
    return env


def command_run(args: argparse.Namespace) -> int:
    if not args.command:
        print("usage: test_neo4j.py run -- <command>", file=sys.stderr)
        return 2
    container = start_container("db")
    try:
        run_id = new_run_id()
        provision(container, run_id)
        print(f"[test_neo4j] disposable {image()} at {container.uri} provisioned", flush=True)
        return subprocess.call(args.command, cwd=ROOT, env=test_environment(container, run_id))
    finally:
        stop_container(container.name)


def command_acceptance(args: argparse.Namespace) -> int:
    targets = args.target or list(DEFAULT_ACCEPTANCE_TARGETS)
    allowed = start_container("allowed")
    decoy = None
    try:
        decoy = start_container("decoy")
        run_id = new_run_id()
        provision(allowed, run_id)
        seed_decoy(decoy)
        base = test_environment(allowed, run_id)

        print("[test_neo4j] 1/2 integration tests on the admitted disposable database", flush=True)
        integration = subprocess.call([sys.executable, "-m", "pytest", "-q", *targets], cwd=ROOT, env=base)

        print("[test_neo4j] 2/2 misconfiguration must be rejected before the first mutation", flush=True)
        env = dict(base)
        env.update({
            "FRACTAL_DECOY_NEO4J_URI": decoy.uri,
            "FRACTAL_DECOY_NEO4J_USER": decoy.user,
            "FRACTAL_DECOY_NEO4J_PASSWORD": decoy.password,
            "FRACTAL_DECOY_CONTAINER": decoy.name,
            "FRACTAL_TEST_CONTAINER": allowed.name,
            "FRACTAL_ISOLATION_TARGETS": " ".join(targets),
        })
        rejection = subprocess.call(
            [sys.executable, "-m", "pytest", "-q", "-rA", "tests/isolation/test_decoy_rejection.py"],
            cwd=ROOT, env=env,
        )
        print(f"[test_neo4j] integration exit={integration} isolation exit={rejection}", flush=True)
        return 0 if integration == 0 and rejection == 0 else 1
    finally:
        stop_container(allowed.name)
        if decoy is not None:
            stop_container(decoy.name)


def command_provision(args: argparse.Namespace) -> int:
    missing = [name for name in (ENV_URI, ENV_USER, ENV_PASSWORD) if not os.environ.get(name)]
    if missing:
        print(f"missing {', '.join(missing)}; NEO4J_* is never used", file=sys.stderr)
        return 2
    run_id = args.run_id or new_run_id()
    driver = GraphDatabase.driver(os.environ[ENV_URI], auth=(os.environ[ENV_USER], os.environ[ENV_PASSWORD]))
    try:
        provision_marker_sync(driver, run_id=run_id, database=os.environ.get(ENV_DATABASE) or "neo4j")
    except IsolationRejected as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    finally:
        driver.close()
    print(f"{ENV_RUN_ID}={run_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="run a command against a fresh disposable database")
    run.add_argument("command", nargs=argparse.REMAINDER)
    acceptance = sub.add_parser("acceptance", help="integration run plus decoy rejection checks")
    acceptance.add_argument("--target", action="append", help="pytest target(s) for the integration step")
    prov = sub.add_parser("provision", help="mark a fresh empty database supplied by other infrastructure")
    prov.add_argument("--run-id")
    args = parser.parse_args(argv)
    if args.action == "run":
        if args.command and args.command[0] == "--":
            args.command = args.command[1:]
        return command_run(args)
    if args.action == "acceptance":
        return command_acceptance(args)
    return command_provision(args)


if __name__ == "__main__":
    raise SystemExit(main())
