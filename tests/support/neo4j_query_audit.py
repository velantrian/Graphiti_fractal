"""Opt-in pytest plugin that records every Neo4j statement a test process sends.

Load with ``-p tests.support.neo4j_query_audit`` and set
``FRACTAL_TEST_NEO4J_QUERY_AUDIT=<path>``. Each driver creation and each
statement (auto-commit, managed or explicit transaction; sync or async) is
appended to the JSONL file with its access mode and target database. Parameter
values and credentials are never recorded.

Used by tests/isolation/test_decoy_rejection.py to show that a rejected
configuration sends nothing but the read-only marker query. The plugin wraps
neo4j driver internals of the version pinned in constraints-ci.txt and fails
loudly if they move, rather than silently recording nothing.
"""
from __future__ import annotations

import contextvars
import functools
import json
import os
import threading

AUDIT_ENV = "FRACTAL_TEST_NEO4J_QUERY_AUDIT"

_mode: contextvars.ContextVar[str | None] = contextvars.ContextVar("neo4j_audit_mode", default=None)
_lock = threading.Lock()


def _write(event: dict) -> None:
    path = os.environ.get(AUDIT_ENV)
    if not path:
        return
    with _lock, open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _query_text(query) -> str:
    text = getattr(query, "text", query)
    return " ".join(str(text).split())


def _install() -> None:
    import neo4j
    from neo4j._async.work import session as async_session
    from neo4j._async.work import transaction as async_tx
    from neo4j._sync.work import session as sync_session
    from neo4j._sync.work import transaction as sync_tx

    def wrap_driver_factory(owner, name):
        original = getattr(owner, name)

        @functools.wraps(original)
        def factory(uri, *args, **kwargs):
            _write({"event": "driver", "api": f"{owner.__name__}.{name}", "uri": str(uri)})
            return original(uri, *args, **kwargs)

        setattr(owner, name, staticmethod(factory) if isinstance(owner.__dict__.get(name), staticmethod) else factory)

    for owner in (neo4j.GraphDatabase, neo4j.AsyncGraphDatabase):
        wrap_driver_factory(owner, "driver")

    def session_meta(session) -> dict:
        config = session._config
        return {"database": config.database, "session_mode": config.default_access_mode}

    # Auto-commit statements.
    async_run = async_session.AsyncSession.run

    @functools.wraps(async_run)
    async def audited_async_run(self, query, parameters=None, **kwargs):
        meta = session_meta(self)
        _write({"event": "query", "kind": "auto-commit", "mode": meta["session_mode"],
                "database": meta["database"], "query": _query_text(query)})
        return await async_run(self, query, parameters, **kwargs)

    async_session.AsyncSession.run = audited_async_run

    sync_run = sync_session.Session.run

    @functools.wraps(sync_run)
    def audited_sync_run(self, query, parameters=None, **kwargs):
        meta = session_meta(self)
        _write({"event": "query", "kind": "auto-commit", "mode": meta["session_mode"],
                "database": meta["database"], "query": _query_text(query)})
        return sync_run(self, query, parameters, **kwargs)

    sync_session.Session.run = audited_sync_run

    # Managed transactions (execute_read/execute_write/driver.execute_query)
    # carry an explicit access mode; explicit transactions use the session default.
    async_managed = async_session.AsyncSession._run_transaction

    @functools.wraps(async_managed)
    async def audited_async_managed(self, access_mode, *args, **kwargs):
        token = _mode.set(access_mode)
        try:
            return await async_managed(self, access_mode, *args, **kwargs)
        finally:
            _mode.reset(token)

    async_session.AsyncSession._run_transaction = audited_async_managed

    sync_managed = sync_session.Session._run_transaction

    @functools.wraps(sync_managed)
    def audited_sync_managed(self, access_mode, *args, **kwargs):
        token = _mode.set(access_mode)
        try:
            return sync_managed(self, access_mode, *args, **kwargs)
        finally:
            _mode.reset(token)

    sync_session.Session._run_transaction = audited_sync_managed

    async_tx_run = async_tx.AsyncTransactionBase.run

    @functools.wraps(async_tx_run)
    async def audited_async_tx_run(self, query, parameters=None, **kwargs):
        _write({"event": "query", "kind": "transaction", "mode": _mode.get() or "session-default",
                "query": _query_text(query)})
        return await async_tx_run(self, query, parameters, **kwargs)

    async_tx.AsyncTransactionBase.run = audited_async_tx_run

    sync_tx_run = sync_tx.TransactionBase.run

    @functools.wraps(sync_tx_run)
    def audited_sync_tx_run(self, query, parameters=None, **kwargs):
        _write({"event": "query", "kind": "transaction", "mode": _mode.get() or "session-default",
                "query": _query_text(query)})
        return sync_tx_run(self, query, parameters, **kwargs)

    sync_tx.TransactionBase.run = audited_sync_tx_run


def pytest_configure(config):  # noqa: ARG001 - pytest hook signature
    if os.environ.get(AUDIT_ENV):
        _install()
        _write({"event": "audit-installed"})
