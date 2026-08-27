from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import simple_chat_agent as chat_module
from core.config import get_config
from simple_chat_agent import SimpleChatAgent


class _SlowMemory:
    user_id = "owner"
    graphiti = object()

    async def build_context_for_query(self, *args, **kwargs):
        await asyncio.sleep(0.05)
        raise AssertionError("timeout should cancel this path")


class _BrokenMemory:
    user_id = "owner"
    graphiti = object()

    async def build_context_for_query(self, *args, **kwargs):
        raise RuntimeError("neo4j unavailable")


def _discard_background_task(coro, **_kwargs):
    coro.close()
    return None


@pytest.fixture(autouse=True)
def _clear_config_cache(monkeypatch):
    get_config.cache_clear()
    monkeypatch.setenv("FRACTAL_MEMORY_RECALL", "always")
    monkeypatch.setenv("FRACTAL_RECALL_TIMEOUT_SECONDS", "0.01")
    yield
    get_config.cache_clear()


@pytest.mark.asyncio
async def test_recall_timeout_degrades_memory_but_still_returns_model_reply(monkeypatch):
    async def fake_llm(messages, context):
        assert "Context from memory:" not in messages[-1]["content"]
        return "ответ без памяти"

    monkeypatch.setattr(chat_module, "llm_chat_response", fake_llm)
    monkeypatch.setattr(chat_module, "spawn", _discard_background_task)

    agent = SimpleChatAgent(SimpleNamespace(), _SlowMemory())
    reply, _, context = await agent.answer_core("Что мы обсуждали раньше?")

    assert reply == "ответ без памяти"
    assert context is not None and context.receipt is not None
    assert context.receipt.status == "DEGRADED_TIMEOUT"
    assert context.receipt.authoritative is False


@pytest.mark.asyncio
async def test_recall_error_degrades_memory_but_still_returns_model_reply(monkeypatch):
    async def fake_llm(messages, context):
        return "ответ после ошибки памяти"

    monkeypatch.setattr(chat_module, "llm_chat_response", fake_llm)
    monkeypatch.setattr(chat_module, "spawn", _discard_background_task)

    agent = SimpleChatAgent(SimpleNamespace(), _BrokenMemory())
    reply, _, context = await agent.answer_core("Напомни прошлое решение")

    assert reply == "ответ после ошибки памяти"
    assert context is not None and context.receipt is not None
    assert context.receipt.status == "DEGRADED_ERROR"
    assert context.receipt.authoritative is False
