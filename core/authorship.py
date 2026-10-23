"""Exact-UUID author-edge attachment; failures are never silently accepted."""

import logging
from core.graphiti_client import get_graphiti_client

logger = logging.getLogger(__name__)


async def attach_author(episode_uuid: str, user_id: str) -> None:
    if not episode_uuid or not user_id:
        raise ValueError("episode_uuid and user_id are required for authorship")
    graphiti = await get_graphiti_client().ensure_ready()
    result = await graphiti.driver.execute_query(
        """
        MERGE (u:User {user_id:$user_id})
        WITH u
        MATCH (e:Episodic {uuid:$episode_uuid})
        MERGE (u)-[:AUTHORED]->(e)
        RETURN e.uuid AS uuid
        """,
        user_id=user_id,
        episode_uuid=episode_uuid,
    )
    if not result.records:
        raise LookupError(f"authorship target episode missing: {episode_uuid}")
    logger.info("Attached author to episode %s", episode_uuid)
