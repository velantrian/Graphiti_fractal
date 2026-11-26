# Test database isolation

Integration tests delete whole graphs and create schema. They may do that only
in a disposable Neo4j that was provisioned for the current run.

## Rule

1. Tests read only `FRACTAL_TEST_NEO4J_URI`, `FRACTAL_TEST_NEO4J_USER`,
   `FRACTAL_TEST_NEO4J_PASSWORD`, optional `FRACTAL_TEST_NEO4J_DATABASE`
   (default `neo4j`) and `FRACTAL_TEST_RUN_ID`. Runtime `NEO4J_*` and `.env`
   are never a fallback. Without test settings the database tests are skipped;
   with `FRACTAL_REQUIRE_TEST_NEO4J=1` (CI and `scripts/test_neo4j.py`) that is
   an error.
2. Test infrastructure (`scripts/test_neo4j.py`) creates one
   `(:FractalTestDatabase {run_id, purpose:'disposable-integration-test'})`
   marker, and only in an **empty** database (no nodes, constraints or
   non-lookup indexes). A database that holds data cannot be marked.
3. Before any write, including Graphiti schema setup and cleanup in `finally`,
   the test process reads the marker in a `READ_ACCESS` session (Neo4j rejects
   writes in that mode) and requires `run_id == FRACTAL_TEST_RUN_ID`.
   Admission runs once per session in `tests/integration/conftest.py`, before
   any Graphiti client exists: graphiti_core's `Neo4jDriver.__init__` schedules
   `build_indices_and_constraints()` as soon as it is constructed.
4. Only after admission are `NEO4J_*` pointed at the admitted database for
   application code under test.
5. Full cleanup goes through `IsolatedGraph.reset()` / `reset_test_graph()`:
   it re-reads the marker, repeats the marker condition inside the write
   transaction and deletes every node **except** the marker.
   `tests/test_neo4j_isolation_guard.py` fails if a database test adds an
   unscoped `MATCH (n) DETACH DELETE n` or reads `NEO4J_*`.

## Commands

| Command | What it does |
|---|---|
| `make test-integration` | Fresh container (random port, password and run id), provision, `pytest tests/integration`, remove container. Needs Docker. |
| `make test-isolation` | The same, plus a decoy database with a synthetic control record and `tests/isolation/test_decoy_rejection.py`. CI runs this. |
| `python scripts/test_neo4j.py provision` | Mark a fresh, empty database supplied by other infrastructure; prints `FRACTAL_TEST_RUN_ID`. Then `make test-integration-provisioned`. |

`FRACTAL_TEST_NEO4J_IMAGE` overrides the image (default: the reviewed
`neo4j:5.26.29-community` pin from `docker-compose.yml`), e.g. for a registry
mirror.

## Evidence produced by `make test-isolation`

For each misconfiguration the integration suite runs in a subprocess with the
`tests.support.neo4j_query_audit` plugin, which logs every statement and its
access mode. The acceptance test requires:

* unmarked decoy (with `NEO4J_*` also pointing at it): every database test
  errors, the only statement sent is one READ marker query, and the decoy's
  nodes, relationships, constraints, indexes and transaction-log checksum are
  unchanged;
* only runtime `NEO4J_*` set: tests skip (or error when required) without
  creating a driver or sending any statement;
* marker of another run: rejected with one READ query, database unchanged;
* provisioning a database with data: refused before any write;
* guarded reset: keeps the marker, survives repeated resets, and a reset with
  a stale run id deletes nothing.

## Limits

* The marker proves which database a process talks to; it does not stop a
  person from deliberately provisioning an empty production database.
* `tests/e2e` and the provider/provenance workflows still use their own
  `NEO4J_*` settings and are not covered by this guard.
