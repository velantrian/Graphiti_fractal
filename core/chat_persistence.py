"""Chat persistence primitives.

The database counter is authoritative for turn ordering. Summary source windows
are read back from persisted episodes, never reconstructed from RAM or synthetic
UUIDs.
"""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any

logger = logging.getLogger(__name__)


async def ensure_counter_constraint(graphiti) -> None:
    await graphiti.driver.execute_query(
        """
        CREATE CONSTRAINT chat_turn_counter_unique IF NOT EXISTS
        FOR (c:ChatTurnCounter)
        REQUIRE (c.user_id, c.conversation_id) IS UNIQUE
        """
    )


async def allocate_turn_index(graphiti, user_id: str, conversation_id: str) -> int:
    """Atomically allocate a unique 1-based turn index or fail closed."""
    await ensure_counter_constraint(graphiti)
    result = await graphiti.driver.execute_query(
        """
        MERGE (c:ChatTurnCounter {user_id:$user_id, conversation_id:$conversation_id})
        ON CREATE SET c.value = 0
        SET c.value = c.value + 1
        RETURN c.value AS turn_index
        """,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    if not result.records:
        raise RuntimeError("turn index allocation returned no record")
    turn_index = result.records[0]["turn_index"]
    if not isinstance(turn_index, int) or turn_index < 1:
        raise RuntimeError(f"invalid turn index allocated: {turn_index!r}")
    return turn_index


async def get_conversation_turn_count(graphiti, user_id: str, conversation_id: str) -> int:
    result = await graphiti.driver.execute_query(
        """
        MATCH (:User {user_id:$user_id})-[:AUTHORED]->(e:Episodic {
            episode_kind:'chat_turn', conversation_id:$conversation_id
        })
        WHERE coalesce(e.deleted, false) = false
        RETURN count(e) AS turn_count
        """,
        user_id=user_id,
        conversation_id=conversation_id,
    )
    return int(result.records[0]["turn_count"]) if result.records else 0


async def fetch_persisted_turn_window(
    graphiti,
    *,
    user_id: str,
    conversation_id: str,
    end_turn_index: int,
    window_size: int = 10,
    wait_timeout: float = 20.0,
    poll_interval: float = 0.2,
) -> list[dict[str, Any]]:
    """Read an exact persisted turn window, waiting briefly for earlier async writes.

    For end_turn_index=20 and window_size=10 the requested range is 11..20.
    An incomplete window is returned only after timeout; callers should not build
    a summary from it.
    """
    start_turn_index = max(1, end_turn_index - window_size + 1)
    expected_count = end_turn_index - start_turn_index + 1
    deadline = monotonic() + max(0.0, wait_timeout)

    query = """
    MATCH (:User {user_id:$user_id})-[:AUTHORED]->(e:Episodic {
        episode_kind:'chat_turn', conversation_id:$conversation_id
    })
    WHERE e.turn_index >= $start_turn_index
      AND e.turn_index <= $end_turn_index
      AND coalesce(e.deleted, false) = false
    RETURN e.uuid AS uuid,
           coalesce(e.content, e.episode_body, '') AS content,
           e.turn_index AS turn_index
    ORDER BY e.turn_index ASC
    """

    while True:
        result = await graphiti.driver.execute_query(
            query,
            user_id=user_id,
            conversation_id=conversation_id,
            start_turn_index=start_turn_index,
            end_turn_index=end_turn_index,
        )
        turns = [
            {
                "uuid": record["uuid"],
                "content": record["content"] or "",
                "turn_index": record["turn_index"],
            }
            for record in result.records
        ]
        if len(turns) == expected_count:
            return turns
        if monotonic() >= deadline:
            logger.warning(
                "Persisted summary window incomplete: conversation=%s expected=%d got=%d range=%d-%d",
                conversation_id,
                expected_count,
                len(turns),
                start_turn_index,
                end_turn_index,
            )
            return turns
        await asyncio.sleep(poll_interval)


async def mark_turns_summarized(
    graphiti,
    *,
    turn_uuids: list[str],
    summary_uuid: str,
) -> int:
    if not turn_uuids:
        return 0
    result = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic)
        WHERE e.uuid IN $turn_uuids
        SET e.summarized = true,
            e.summary_uuid = $summary_uuid
        RETURN count(e) AS updated
        """,
        turn_uuids=turn_uuids,
        summary_uuid=summary_uuid,
    )
    return int(result.records[0]["updated"]) if result.records else 0
