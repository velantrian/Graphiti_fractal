from collections import deque

import pytest
from fastapi.testclient import TestClient

from app import app
from core.conversation_buffer import _conversation_buffers, get_user_conversation_buffer
from core.memory_ops import _recent_memories

TEST_TOKEN = "buffer-test-token"
TEST_USER = "test_user"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FRACTAL_API_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("FRACTAL_USER_ID", TEST_USER)
    with TestClient(app) as test_client:
        yield test_client


def _auth_headers():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def test_buffer_clear_endpoint(client):
    buffer = get_user_conversation_buffer(TEST_USER)
    buffer.add_message("user", "Hello")
    buffer.add_message("assistant", "Hi")
    _recent_memories.setdefault(TEST_USER, deque()).append({"text": "Something"})

    response = client.post(
        "/buffer/clear",
        headers=_auth_headers(),
        json={"user_id": TEST_USER},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["cleared"]["conversation_buffer"] == 2
    assert data["cleared"]["recent_memories"] == 1
    assert TEST_USER not in _conversation_buffers
    assert TEST_USER not in _recent_memories


def test_buffer_clear_uses_server_owner_when_user_id_is_omitted(client):
    _conversation_buffers.pop(TEST_USER, None)
    _recent_memories.pop(TEST_USER, None)

    response = client.post(
        "/buffer/clear",
        headers=_auth_headers(),
        json={},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == TEST_USER
    assert data["cleared"]["conversation_buffer"] == 0
    assert data["cleared"]["recent_memories"] == 0
