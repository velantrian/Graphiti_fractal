"""Central model policy for Fractal Memory.

Keep active runtime defaults in one place. Historical model references belong in
docs/AI_MODEL_EVOLUTION.md and must not silently become runtime defaults again.
"""

MODEL_POLICY_AS_OF = "2026-08-24"

# Current OpenAI runtime defaults.
# Terra balances capability and cost for interactive + extraction workloads.
# Luna is the lower-cost model for Graphiti's simpler/smaller prompts.
DEFAULT_OPENAI_MODEL = "gpt-5.6-terra"
DEFAULT_OPENAI_SMALL_MODEL = "gpt-5.6-luna"
DEFAULT_OPENAI_REASONING_EFFORT = "none"

# OpenAI still lists the text-embedding-3 family as the current embedding family.
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


def is_reasoning_model(model: str) -> bool:
    """Return whether optional sampling params should be omitted for the model."""
    normalized = (model or "").strip().lower()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4"))
