import asyncio
from datetime import datetime, timedelta, timezone

from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF
from graphiti_core.search.search_filters import SearchFilters, DateFilter, ComparisonOperator

from core import get_graphiti_client


async def get_l1_context(graphiti, user_context: str, hours_back: int = 24) -> str:
    """Return recent, current episodic context for the requested time window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    search_filter = SearchFilters(
        invalid_at=[[DateFilter(date=None, comparison_operator=ComparisonOperator.is_null)]]
    )

    result = await graphiti.search_(
        query=user_context,
        config=COMBINED_HYBRID_SEARCH_RRF,
        search_filter=search_filter,
    )

    recent = []
    for episode in getattr(result, "episodes", []) or []:
        ts = getattr(episode, "reference_time", None) or getattr(episode, "created_at", None)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            recent.append((ts, episode))

    recent.sort(key=lambda item: item[0], reverse=True)
    lines = [f"📋 L1 Recent Context (last {hours_back}h):", ""]
    if not recent:
        lines.append("No recent matching episodes found.")
        return "\n".join(lines)

    for ts, episode in recent[:10]:
        text = (
            getattr(episode, "content", None)
            or getattr(episode, "summary", None)
            or getattr(episode, "name", None)
            or ""
        )
        text = " ".join(str(text).split())
        if len(text) > 320:
            text = text[:317] + "..."
        lines.append(f"• {ts.isoformat()} — {text}")

    return "\n".join(lines)


async def test_l1():
    graphiti_client = get_graphiti_client()
    graphiti = await graphiti_client.ensure_ready()
    context = await get_l1_context(graphiti, "Fractal Memory development", hours_back=48)
    print(context)


if __name__ == "__main__":
    asyncio.run(test_l1())
