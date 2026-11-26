"""Explicit fake revision reads for unrelated ingest/chat contracts.

The dedicated fence tests and Neo4j integration exercise the real boundary.
"""
import pytest
from unittest.mock import AsyncMock


@pytest.fixture(autouse=True)
def mock_ingest_revision(monkeypatch):
    monkeypatch.setattr("knowledge.ingest.read_erasure_state", AsyncMock(return_value=(0, False)))
    monkeypatch.setattr("knowledge.ingest.require_erasure_revision", AsyncMock())
