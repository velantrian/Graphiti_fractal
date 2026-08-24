import asyncio
from datetime import datetime, timedelta, timezone

from core import get_graphiti_client
from core.datetime_utils import normalize_dt
from core.instance import get_instance_user_id
from core.memory_ops import MemoryOps


async def get_l1_context(graphiti, user_context: str, hours_back: int = 24) -> str:
    """Return recent episodic context using the canonical scoped retrieval service."""
    if hours_back < 1:
        raise ValueError("hours_back must be >= 1")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    result = await MemoryOps(graphiti, get_instance_user_id()).search_memory(
        user_context,
        limit=20,
        include_episodes=True,
        include_entities=False,
    )

    recent = []
    for episode in result.episodes:
        timestamp = normalize_dt(episode.get("created_at"))
        if timestamp is not None and timestamp >= cutoff:
            recent.append((timestamp, episode))

    recent.sort(key=lambda item: item[0], reverse=True)
    lines = [f"📋 L1 Recent Context (last {hours_back}h):", ""]
    if not recent:
        lines.append("No recent matching episodes found.")
        return "\n".join(lines)

    for timestamp, episode in recent[:10]:
        text = " ".join((episode.get("content") or episode.get("name") or "").split())
        if len(text) > 320:
            text = text[:317] + "..."
        lines.append(f"• {timestamp.isoformat()} — {text}")

    return "\n".join(lines)


async def test_l1():
    graphiti = await get_graphiti_client().ensure_ready()
    print(await get_l1_context(graphiti, "Fractal Memory development", hours_back=48))


if __name__ == "__main__":
    asyncio.run(test_l1())
