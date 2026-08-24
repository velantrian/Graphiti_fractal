"""Central model policy for Fractal Memory.

Keep active runtime defaults in one place. Historical model references belong in
docs/AI_MODEL_EVOLUTION.md and must not silently become runtime defaults again.
"""

MODEL_POLICY_AS_OF = "2026-08-24"

# Current OpenAI runtime defaults.
# Terra balances capability and cost for interactive + extraction workloads.
# Luna is the lower-cost model for summaries and Graphiti's smaller prompts.
DEFAULT_CHAT_MODEL = "gpt-5.6-terra"
DEFAULT_SUMMARY_MODEL = "gpt-5.6-luna"
DEFAULT_GRAPHITI_MODEL = "gpt-5.6-terra"
DEFAULT_GRAPHITI_SMALL_MODEL = "gpt-5.6-luna"
DEFAULT_OPENAI_REASONING_EFFORT = "none"

# Kept stable because this remains a current OpenAI embedding family and changing
# embedding dimensions/model identity would require an explicit reindex decision.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


def default_model_for_context(context: str | None) -> str:
    """Resolve the built-in model default for a local LLM workload."""
    normalized = (context or "").strip().lower()
    if normalized in {"summary", "general"}:
        return DEFAULT_SUMMARY_MODEL
    return DEFAULT_CHAT_MODEL


def is_reasoning_model(model: str) -> bool:
    """Return whether optional sampling params should be omitted for the model."""
    normalized = (model or "").strip().lower()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4"))
