import logging
import os

from openai import AsyncOpenAI

from core.model_policy import default_model_for_context, is_reasoning_model

logger = logging.getLogger(__name__)

_aclient = None


def get_async_client():
    global _aclient
    if _aclient is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, LLM calls will fail.")
            return None
        _aclient = AsyncOpenAI(api_key=api_key)
    return _aclient


def _select_model_for_context(context: str | None) -> str:
    """Select an OpenAI model for the workload with explicit env overrides."""
    ctx = (context or "").strip().upper()
    if ctx:
        env_key = f"{ctx}_OPENAI_MODEL"
        value = (os.getenv(env_key) or "").strip()
        if value:
            return value

    value = (os.getenv("OPENAI_MODEL") or "").strip()
    if value:
        return value

    return default_model_for_context(context)


async def llm_summarize(text_list: list[str], context: str = "general") -> str:
    """Summarize facts into a bounded high-level abstraction."""
    client = get_async_client()
    if not client:
        return "LLM service unavailable due to missing API key."

    model = _select_model_for_context(context)
    joined_text = "\n- ".join(text_list)
    prompt = (
        "You are a memory consolidation system for an AI agent.\n"
        f"Context: {context}\n\n"
        "Below is a list of recent episodic memories and facts:\n"
        f"- {joined_text}\n\n"
        "Task: Synthesize these details into 3-5 high-level abstract insights or patterns. "
        "Ignore trivial details. Focus on what changed, what was decided, or what was learned.\n"
        "Output format: Bullet points."
    )

    try:
        request = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        # GPT-5 family models reject legacy sampling parameters such as temperature.
        if not is_reasoning_model(model):
            request["temperature"] = 0.3
        resp = await client.chat.completions.create(**request)
        return resp.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        logger.error("LLM error with model %s: %s", model, exc)
        return f"Error generating summary: {exc}"


async def llm_chat_response(messages: list[dict], context: str = "chat") -> str:
    """Generate a chat response using the configured current model policy."""
    client = get_async_client()
    if not client:
        return "LLM service unavailable due to missing API key."

    model = _select_model_for_context(context)

    try:
        # Keep the Chat Completions call minimal for compatibility across GPT-5.6 tiers.
        resp = await client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        logger.error("LLM chat error with model %s: %s", model, msg)
        return f"Извините, произошла ошибка при генерации ответа: {msg[:120]}"
