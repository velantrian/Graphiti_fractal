"""Explicit fake admission for unrelated formatting and ranking unit tests."""
import pytest
from unittest.mock import AsyncMock


@pytest.fixture(autouse=True)
def mock_admission(monkeypatch):
    async def admit(_graphiti, candidates):
        return {(item["type"], item["uuid"], item["group_id"]) for item in candidates}
    monkeypatch.setattr("core.memory_ops.admit_search_candidates", AsyncMock(side_effect=admit))
