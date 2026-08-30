from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import core.conversation_buffer as buffer_module
from core.config import AppSettings


def test_factory_honors_configured_message_capacity(monkeypatch):
    buffer_module._conversation_buffers.clear()
    monkeypatch.setattr(
        "core.config.get_config",
        lambda: SimpleNamespace(app=SimpleNamespace(conversation_buffer_max_messages=4)),
    )

    buffer = buffer_module.get_user_conversation_buffer("owner")
    for index in range(3):
        buffer.add_turn(f"u{index}", f"a{index}")

    assert buffer.buffer.maxlen == 4
    assert buffer.get_recent_messages(10) == [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]


def test_get_recent_turns_returns_latest_complete_turns():
    buffer = buffer_module.ConversationBuffer(max_messages=10)
    for index in range(4):
        buffer.add_turn(f"u{index}", f"a{index}")

    assert buffer.get_recent_turns(2) == [
        {"user": "u2", "assistant": "a2"},
        {"user": "u3", "assistant": "a3"},
    ]


def test_negative_configured_message_capacity_fails_at_config_boundary():
    with pytest.raises(ValidationError):
        AppSettings(CONVERSATION_BUFFER_MAX_MESSAGES=-1)
