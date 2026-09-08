import pytest

import simple_chat_agent
from core.conversation_buffer import clear_user_buffer, get_user_conversation_buffer
from core.llm import llm_chat_response
from simple_chat_agent import SimpleChatAgent


class _ResponseMessage:
    def __init__(self, content: str):
        self.content = content


class _Choice:
    def __init__(self, content: str):
        self.message = _ResponseMessage(content)


class _Response:
    def __init__(self, content: str):
        self.choices = [_Choice(content)]


class _Completions:
    def __init__(self, *, content: str | None = None, error: Exception | None = None):
        self.content = content
        self.error = error
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return _Response(self.content or "")


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class _Client:
    def __init__(self, completions):
        self.chat = _Chat(completions)


class _Memory:
    def __init__(self, user_id: str):
        self.user_id = user_id


@pytest.mark.asyncio
async def test_llm_chat_response_uses_injected_client(monkeypatch):
    completions = _Completions(content="ok")
    client = _Client(completions)

    monkeypatch.setattr(
        "core.llm.get_async_client",
        lambda: (_ for _ in ()).throw(AssertionError("global client must not be used")),
    )

    response = await llm_chat_response(
        [{"role": "user", "content": "hello"}],
        context="chat",
        client=client,
    )

    assert response == "ok"
    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_provider_failure_is_not_buffered_or_persisted(monkeypatch):
    user_id = "chat-failure-hygiene"
    clear_user_buffer(user_id)
    completions = _Completions(error=RuntimeError("provider down"))
    client = _Client(completions)
    memory = _Memory(user_id)
    agent = SimpleChatAgent(client, memory)
    spawned = []

    monkeypatch.setattr(simple_chat_agent, "should_recall", lambda *_args, **_kwargs: (False, "test"))
    monkeypatch.setattr(simple_chat_agent, "spawn", lambda *args, **kwargs: spawned.append((args, kwargs)))

    reply, conversation_text, context = await agent.answer_core("test message")

    assert reply == "Извините, произошла ошибка при обработке запроса. Попробуйте ещё раз."
    assert "provider down" not in reply
    assert "provider down" not in conversation_text
    assert context is None
    assert spawned == []
    assert get_user_conversation_buffer(user_id).get_recent_messages() == []

    clear_user_buffer(user_id)
