"""Explicit fake revision reads for unrelated ingest/chat contracts.

The dedicated fence tests and Neo4j integration exercise the real boundary.
"""
import pytest
from unittest.mock import AsyncMock


@pytest.fixture(autouse=True)
def mock_ingest_revision(monkeypatch):
    monkeypatch.setattr("knowledge.ingest.read_erasure_state", AsyncMock(return_value=(0, False)))
    monkeypatch.setattr("knowledge.ingest.require_erasure_revision", AsyncMock())
    monkeypatch.setattr("knowledge.ingest.require_group_writable", AsyncMock())
    monkeypatch.setattr("knowledge.ingest.live_previous_episode_uuids", AsyncMock(return_value=None))



@pytest.fixture(autouse=True)
def mock_chat_erasure(monkeypatch):
    """Unrelated chat behavior tests explicitly simulate an unchanged revision."""
    monkeypatch.setattr("simple_chat_agent.read_erasure_state", AsyncMock(return_value=(0, False)))
    monkeypatch.setattr("simple_chat_agent.require_erasure_state", AsyncMock())
