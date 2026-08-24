import os
import uuid

import pytest

from core.graphiti_client import get_graphiti_client, reset_graphiti_client
from core.memory_ops import MemoryOps


@pytest.mark.asyncio
async def test_provider_backed_ingest_and_recall_roundtrip():
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.fail("OPENAI_API_KEY is required for provider-backed E2E")

    marker = f"fractal-e2e-{uuid.uuid4()}"
    text = f"The bounded provider-backed Fractal E2E marker is {marker}."

    reset_graphiti_client()
    graphiti = await get_graphiti_client(force_new=True).ensure_ready()
    memory = MemoryOps(graphiti, os.environ.get("FRACTAL_USER_ID", "e2e-owner"))

    result = await memory.ingest_pipeline(
        text,
        source_description="provider_e2e",
        memory_type="knowledge",
    )
    assert result["status"] == "ok", result
    assert result["added"] >= 1, result

    recalled = await memory.search_memory(
        marker,
        scopes=["knowledge"],
        limit=10,
        include_episodes=True,
        include_entities=True,
    )
    episode_texts = [str(item.get("content") or "") for item in recalled.episodes]
    assert any(marker in content for content in episode_texts), recalled

    # E2E asserts persistence + retrieval. It does not promote, authorize, or
    # transform the episode into a higher-authority fact.
