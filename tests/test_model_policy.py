from core.config import LLMSettings
from core.llm import _select_model_for_context
from core.model_policy import (
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_GRAPHITI_MODEL,
    DEFAULT_GRAPHITI_SMALL_MODEL,
    DEFAULT_SUMMARY_MODEL,
    MODEL_POLICY_AS_OF,
    is_reasoning_model,
)


def _clear_model_env(monkeypatch):
    for name in (
        "OPENAI_MODEL",
        "CHAT_OPENAI_MODEL",
        "SUMMARY_OPENAI_MODEL",
        "GENERAL_OPENAI_MODEL",
        "GRAPHITI_OPENAI_MODEL",
        "GRAPHITI_OPENAI_SMALL_MODEL",
        "GRAPHITI_OPENAI_REASONING_EFFORT",
        "EMBEDDING_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_current_model_policy_defaults(monkeypatch):
    _clear_model_env(monkeypatch)

    assert MODEL_POLICY_AS_OF == "2026-08-24"
    assert DEFAULT_CHAT_MODEL == "gpt-5.6-terra"
    assert DEFAULT_SUMMARY_MODEL == "gpt-5.6-luna"
    assert DEFAULT_GRAPHITI_MODEL == "gpt-5.6-terra"
    assert DEFAULT_GRAPHITI_SMALL_MODEL == "gpt-5.6-luna"
    assert DEFAULT_EMBEDDING_MODEL == "text-embedding-3-small"

    settings = LLMSettings(_env_file=None)
    assert settings.openai_model == DEFAULT_CHAT_MODEL
    assert settings.graphiti_openai_model == DEFAULT_GRAPHITI_MODEL
    assert settings.graphiti_openai_small_model == DEFAULT_GRAPHITI_SMALL_MODEL
    assert settings.graphiti_openai_reasoning_effort == "none"
    assert settings.embedding_model == DEFAULT_EMBEDDING_MODEL


def test_workload_defaults(monkeypatch):
    _clear_model_env(monkeypatch)

    assert _select_model_for_context("chat") == DEFAULT_CHAT_MODEL
    assert _select_model_for_context("summary") == DEFAULT_SUMMARY_MODEL
    assert _select_model_for_context("general") == DEFAULT_SUMMARY_MODEL


def test_context_override_wins(monkeypatch):
    _clear_model_env(monkeypatch)
    monkeypatch.setenv("OPENAI_MODEL", "global-model")
    monkeypatch.setenv("SUMMARY_OPENAI_MODEL", "summary-model")

    assert _select_model_for_context("chat") == "global-model"
    assert _select_model_for_context("summary") == "summary-model"


def test_reasoning_family_detection():
    assert is_reasoning_model("gpt-5.6-terra")
    assert is_reasoning_model("gpt-5.6-luna")
    assert is_reasoning_model("o3")
    assert not is_reasoning_model("gpt-4o-mini")
