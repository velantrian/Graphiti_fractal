"""L3 synthesis built from Graphiti community context."""

import asyncio
import logging

from core import get_graphiti_client
from core.instance import get_instance_user_id
from core.llm import llm_chat_response
from core.memory_ops import MemoryOps
from core.provenance import build_provenance_record
from core.provenance_persistence import persist_provenance_metadata
from layers.l2_semantic import get_l2_semantic_context_with_sources

logger = logging.getLogger(__name__)


async def build_l3_profile(graphiti, entity_name: str, user_id: str | None = None) -> str | None:
    """Synthesize and persist one bounded high-level profile with exact L2 lineage."""
    l2_context, source_ids = await get_l2_semantic_context_with_sources(graphiti, entity_name)
    if not l2_context:
        logger.warning("No L2 community context for %r", entity_name)
        return None
    if not source_ids:
        raise RuntimeError("L3 provenance requires exact L2 community source UUIDs")

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
    profile = (await llm_chat_response([{"role": "user", "content": prompt}], context="l3_build")).strip()
    if not profile:
        raise RuntimeError("L3 synthesis returned an empty profile")
    if len(profile) > 1400:
        profile = profile[:1397].rstrip() + "..."

    owner = user_id or get_instance_user_id()
    result = await MemoryOps(graphiti, owner).ingest_pipeline(profile, source_description=f"l3_profile:{entity_name}", memory_type="knowledge")
    if result.get("status") != "ok":
        raise RuntimeError(f"L3 profile ingest failed: {result}")
    if int(result.get("added", 0)) == 0:
        logger.info("L3 profile already existed; no new provenance mutation required for %r", entity_name)
        return profile

    resolved = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic)
        WHERE e.source_description=$source
          AND coalesce(e.content, e.episode_body, '')=$content
          AND coalesce(e.deleted,false)=false
        RETURN e.uuid AS uuid
        LIMIT 2
        """,
        source=f"l3_profile:{entity_name}",
        content=profile,
    )
    uuids = [str(record["uuid"]) for record in resolved.records if record["uuid"]]
    if len(uuids) != 1:
        raise RuntimeError("new L3 profile did not resolve to exactly one persisted episode")

    provenance = build_provenance_record(kind="l3_profile", source_ids=source_ids, activity="l3_semantic_synthesis", agent="fractal:l3", payload=profile)
    await persist_provenance_metadata(graphiti, uuids[0], {"provenance_id": provenance["provenance_id"], "provenance_activity": provenance["activity"], "provenance_agent": provenance["agent"], "payload_sha256": provenance["payload_sha256"], "derived_source_ids": source_ids, "authoritative_fact": False})
    logger.info("L3 profile persisted for %r with provenance", entity_name)
    return profile


async def get_l3_fractal_context(graphiti, entity_name: str) -> str:
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
