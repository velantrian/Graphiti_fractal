import asyncio
from types import SimpleNamespace

import pytest

from core.migrations import Migration, apply_migrations


class _MigrationDriver:
    def __init__(self, ledger):
        self.ledger = ledger
        self.calls = []

    async def execute_query(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if "RETURN m.migration_id AS id, m.checksum AS checksum" in query:
            return SimpleNamespace(
                records=[{"id": migration_id, "checksum": checksum} for migration_id, checksum in self.ledger.items()]
            )
        return SimpleNamespace(records=[])


class _Graphiti:
    def __init__(self, ledger):
        self.driver = _MigrationDriver(ledger)


def test_applied_migration_checksum_drift_fails_before_statements_execute():
    graphiti = _Graphiti({"001_init.cypher": "recorded-checksum"})
    migration = Migration(
        migration_id="001_init.cypher",
        checksum="changed-checksum",
        statements=["CREATE (:ShouldNeverRun)"],
    )

    with pytest.raises(RuntimeError, match="migration checksum mismatch"):
        asyncio.run(apply_migrations(graphiti, migrations=[migration]))

    executed_queries = [query for query, _ in graphiti.driver.calls]
    assert "CREATE (:ShouldNeverRun)" not in executed_queries


def test_applied_migration_with_matching_checksum_is_skipped():
    graphiti = _Graphiti({"001_init.cypher": "same-checksum"})
    migration = Migration(
        migration_id="001_init.cypher",
        checksum="same-checksum",
        statements=["CREATE (:ShouldNeverRun)"],
    )

    result = asyncio.run(apply_migrations(graphiti, migrations=[migration]))

    assert result == {"applied": 0, "skipped": 1, "total": 1}
    executed_queries = [query for query, _ in graphiti.driver.calls]
    assert "CREATE (:ShouldNeverRun)" not in executed_queries


def test_legacy_applied_row_without_checksum_fails_closed():
    graphiti = _Graphiti({"001_init.cypher": None})
    migration = Migration(
        migration_id="001_init.cypher",
        checksum="current-checksum",
        statements=["CREATE (:ShouldNeverRun)"],
    )

    with pytest.raises(RuntimeError, match="migration checksum mismatch"):
        asyncio.run(apply_migrations(graphiti, migrations=[migration]))
