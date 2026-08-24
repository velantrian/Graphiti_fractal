from collections import deque

import pytest
from fastapi.testclient import TestClient

from app import app
from core.conversation_buffer import _conversation_buffers, get_user_conversation_buffer
from core.memory_ops import _recent_memories

TEST_TOKEN = "buffer-test-token"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FRACTAL_API_TOKEN", TEST_TOKEN)
    with TestClient(app) as test_client:
        yield test_client


def _auth_headers():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def test_buffer_clear_endpoint(client):
    user_id = "test_user"
    buffer = get_user_conversation_buffer(user_id)
    buffer.add_message("user", "Hello")
    buffer.add_message("assistant", "Hi")

    _recent_memories.setdefault(user_id, deque()).append({"text": "Something"})

    assert len(_conversation_buffers[user_id].buffer) == 2
    assert len(_recent_memories[user_id]) == 1

    response = client.post(
        "/buffer/clear",
        headers=_auth_headers(),
        json={"user_id": user_id},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["cleared"]["conversation_buffer"] == 2
    assert data["cleared"]["recent_memories"] == 1
    assert user_id not in _conversation_buffers
    assert user_id not in _recent_memories


def test_buffer_clear_empty(client):
    user_id = "empty_user"
    _conversation_buffers.pop(user_id, None)
    _recent_memories.pop(user_id, None)

    response = client.post(
        "/buffer/clear",
        headers=_auth_headers(),
        json={"user_id": user_id},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["cleared"]["conversation_buffer"] == 0
    assert data["cleared"]["recent_memories"] == 0
