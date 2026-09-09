from core.config import MemorySettings, Settings


def test_legacy_chat_flags_are_not_active_memory_settings(monkeypatch):
    monkeypatch.setenv("CHAT_SAVE_EPISODES", "1")
    monkeypatch.setenv("CHAT_SAVE_BOT_EPISODES", "true")
    monkeypatch.setenv("CHAT_USE_GRAPHITI_SEARCH", "yes")

    memory = MemorySettings()

    assert not hasattr(memory, "chat_save_episodes")
    assert not hasattr(memory, "chat_save_bot_episodes")
    assert not hasattr(memory, "chat_use_graphiti_search")

    legacy = Settings()
    assert legacy.CHAT_SAVE_EPISODES is True
    assert legacy.CHAT_SAVE_BOT_EPISODES is True
    assert legacy.CHAT_USE_GRAPHITI_SEARCH is True
