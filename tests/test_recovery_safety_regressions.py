"""Fail-closed regression contracts for provenance and portable imports."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core import authorship
from core.memory_import import MAX_IMPORT_ENTRIES, build_import_plan
from core.memory_ops import MemoryOps, _recent_memories


def test_external_import_refuses_silent_truncation(tmp_path):
    file = tmp_path / "export.json"
    file.write_text(json.dumps([f"entry-{i}" for i in range(MAX_IMPORT_ENTRIES + 1)]), encoding="utf-8")
    with pytest.raises(ValueError, match="refusing partial import"):
        build_import_plan(str(file))


def test_attach_author_rejects_missing_ids():
    with pytest.raises(ValueError, match="required"):
        asyncio.run(authorship.attach_author("", "owner"))


def test_attach_author_fails_if_episode_missing(monkeypatch):
    driver = SimpleNamespace(execute_query=AsyncMock(return_value=SimpleNamespace(records=[])))
    client = SimpleNamespace(ensure_ready=AsyncMock(return_value=SimpleNamespace(driver=driver)))
    monkeypatch.setattr(authorship, "get_graphiti_client", lambda: client)
    with pytest.raises(LookupError, match="target episode missing"):
        asyncio.run(authorship.attach_author("missing", "owner"))


def test_recent_memory_is_not_recorded_on_failed_ingest(monkeypatch):
    ops = MemoryOps(SimpleNamespace(), "test-no-ghost")
    _recent_memories.pop("test-no-ghost", None)
    async def failed(*args, **kwargs):
        raise RuntimeError("storage failure")
    monkeypatch.setattr(ops, "ingest_pipeline", failed)
    with pytest.raises(RuntimeError, match="storage failure"):
        asyncio.run(ops.remember_text("Remember this", memory_type="personal"))
    assert "test-no-ghost" not in _recent_memories
