"""Deterministic contract tests for the current SimpleChatAgent answer path."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import simple_chat_agent as chat_module
from core.conversation_buffer import clear_user_buffer, get_user_conversation_buffer
from core.types import ContextResult
from simple_chat_agent import SimpleChatAgent


def _discard_background_task(coro, **_kwargs):
    coro.close()
    return None


@pytest.mark.asyncio
async def test_answer_flow_uses_scoped_recall_and_injected_client(monkeypatch):
    user_id = "simple-chat-agent-contract"
    clear_user_buffer(user_id)

    build_context = AsyncMock(
        return_value=ContextResult(
            text="Mock context",
            token_estimate=50,
            sources={"episodes": 1},
            source_ids=["episode-1"],
        )
    )
    memory = SimpleNamespace(
        user_id=user_id,
        graphiti=object(),
        build_context_for_query=build_context,
    )
    injected_client = object()
    seen = {}

    async def fake_llm(messages, context, client=None):
        seen["messages"] = messages
        seen["context"] = context
        seen["client"] = client
        return "Mock LLM response"

    monkeypatch.setattr(chat_module, "should_recall", lambda *_args, **_kwargs: (True, "test"))
    monkeypatch.setattr(chat_module, "llm_chat_response", fake_llm)
    monkeypatch.setattr(chat_module, "spawn", _discard_background_task)

    agent = SimpleChatAgent(injected_client, memory)
    response = await agent.answer("Test question")

    assert response == "Mock LLM response"
    build_context.assert_awaited_once_with(
        "Test question",
        scopes=["personal", "project", "knowledge", "experience"],
        max_tokens=2000,
        include_episodes=True,
        include_entities=True,
    )
    assert seen["context"] == "chat"
    assert seen["client"] is injected_client
    assert "Mock context" in seen["messages"][-1]["content"]
    assert get_user_conversation_buffer(user_id).get_recent_messages() == [
        {"role": "user", "content": "Test question"},
        {"role": "assistant", "content": "Mock LLM response"},
    ]

    clear_user_buffer(user_id)
