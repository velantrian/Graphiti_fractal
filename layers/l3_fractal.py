"""L3 synthesis built from Graphiti community context."""

import asyncio
import logging

from core import get_graphiti_client
from core.instance import get_instance_user_id
from core.llm import llm_chat_response
from core.memory_ops import MemoryOps
from layers.l2_semantic import get_l2_semantic_context

logger = logging.getLogger(__name__)


async def build_l3_profile(
    graphiti,
    entity_name: str,
    user_id: str | None = None,
) -> str | None:
    """Synthesize and persist one bounded high-level profile for an entity."""
    l2_context = await get_l2_semantic_context(graphiti, entity_name)
    if not l2_context:
        logger.warning("No L2 community context for %r", entity_name)
        return None

    prompt = f"""Ты анализируешь семантические сообщества памяти.

Сущность: {entity_name}

L2 context:
{l2_context}

Сделай краткий L3-профиль на русском языке. Отделяй наблюдаемое от вывода.
Включи только:
1. системную роль;
2. ключевые ответственности/темы;
3. устойчивые связи;
4. направление изменений, только если оно действительно следует из контекста.

Не добавляй фактов, которых нет в L2 context. Объём — до 900 символов."""
    profile = (
        await llm_chat_response([{"role": "user", "content": prompt}], context="l3_build")
    ).strip()
    if not profile:
        raise RuntimeError("L3 synthesis returned an empty profile")

    # Keep one bounded artifact so retrieval of the latest profile is unambiguous.
    if len(profile) > 1400:
        logger.warning("L3 profile exceeded bound; truncating from %d chars", len(profile))
        profile = profile[:1397].rstrip() + "..."

    owner = user_id or get_instance_user_id()
    result = await MemoryOps(graphiti, owner).ingest_pipeline(
        profile,
        source_description=f"l3_profile:{entity_name}",
        memory_type="knowledge",
    )
    if result.get("status") != "ok":
        raise RuntimeError(f"L3 profile ingest failed: {result}")
    logger.info("L3 profile persisted for %r", entity_name)
    return profile


async def get_l3_fractal_context(graphiti, entity_name: str) -> str:
    """Retrieve the most recent non-deleted L3 profile artifact."""
    source_description = f"l3_profile:{entity_name}"
    result = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic)
        WHERE e.source_description=$source
          AND coalesce(e.deleted, false)=false
        RETURN coalesce(e.content, e.episode_body, '') AS content,
               coalesce(e.created_at, e.valid_at) AS created_at
        ORDER BY coalesce(e.created_at, e.valid_at) DESC
        LIMIT 1
        """,
        source=source_description,
    )
    if not result.records:
        return f"No L3 profile found for '{entity_name}'. Run `python main.py l3-build \"{entity_name}\"`."

    record = result.records[0]
    return f"🌀 L3 FRACTAL PROFILE (Generated {record['created_at']}):\n\n{record['content']}"


async def test_l3():
    graphiti = await get_graphiti_client().ensure_ready()
    print(await get_l3_fractal_context(graphiti, "Sergey"))


if __name__ == "__main__":
    asyncio.run(test_l3())
