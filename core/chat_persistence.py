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

from core.erasure_guard import (
    LOCK_ERASURE_CONTROL, ErasureFenceChanged, ensure_erasure_constraints, visible_node,
)

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
        MATCH (e:Episodic {
            episode_kind:'chat_turn', conversation_id:$conversation_id
        })
        WHERE (EXISTS { MATCH (e)-[:STORED_FOR]->(:User {user_id:$user_id}) }
            OR EXISTS { MATCH (:User {user_id:$user_id})-[:AUTHORED]->(e) })
          AND """ + visible_node("e") + """
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
    expected_revision: int | None = None,
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
    MATCH (e:Episodic {
        episode_kind:'chat_turn', conversation_id:$conversation_id
    })
    WHERE (EXISTS { MATCH (e)-[:STORED_FOR]->(:User {user_id:$user_id}) }
        OR EXISTS { MATCH (:User {user_id:$user_id})-[:AUTHORED]->(e) })
      AND e.turn_index >= $start_turn_index
      AND e.turn_index <= $end_turn_index
      AND """ + visible_node("e") + """
      AND ($expected_revision IS NULL OR
          (e.chat_finalized=true AND e.chat_erasure_revision <= $expected_revision))
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
            expected_revision=expected_revision,
        )
        turns = [
            {
                "uuid": record["uuid"],
                "content": record["content"] or "",
                "turn_index": record["turn_index"],
            }
            for record in result.records
        ]
        if len(turns) == expected_count and [turn["turn_index"] for turn in turns] == list(range(start_turn_index, end_turn_index + 1)) and len({turn["uuid"] for turn in turns}) == expected_count:
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


# New chat writes use STORED_FOR, not owner authorship. Legacy AUTHORED links
# remain readable outside quarantine; no retrospective attribution is inferred.
_CHAT_FIELDS = {
    "chat_turn": {
        "episode_kind", "conversation_id", "turn_index", "is_correction",
        "summarized", "recall_derived", "recall_context_sha256",
        "recall_query_sha256", "recall_source_ids", "recall_guard_authoritative",
    },
    "chat_summary": {
        "episode_kind", "conversation_id", "covers_turns", "summarized_turns",
        "provenance_id", "provenance_activity", "provenance_agent",
        "payload_sha256", "derived_source_ids",
    },
}


async def finalize_chat_episode(
    graphiti, *, episode_uuid: str, user_id: str, group_id: str,
    expected_state: tuple[int, bool], metadata: dict,
) -> None:
    """Atomically admit exact raw chat episode and metadata at its original fence.

    The upstream add_episode transaction is separate. A denied finalization
    cannot certify a raw write. Summary sources are checked and marked in the
    same transaction; no content lookup or inferred author identity is used.
    """
    kind = metadata.get("episode_kind")
    if kind not in _CHAT_FIELDS or set(metadata) != _CHAT_FIELDS[kind]:
        raise ValueError("invalid chat metadata contract")
    if not episode_uuid or not user_id or not group_id or not metadata["conversation_id"]:
        raise ValueError("exact chat identity is required")
    revision, quarantined = expected_state
    if type(revision) is not int or revision < 0 or type(quarantined) is not bool:
        raise ValueError("invalid erasure state")
    sources = []
    if kind == "chat_summary":
        sources = metadata["summarized_turns"]
        if (not isinstance(sources, list) or len(sources) != 10
                or any(not isinstance(x, str) or not x for x in sources)
                or len(set(sources)) != 10 or metadata["derived_source_ids"] != sources):
            raise ValueError("summary requires ten exact distinct source UUIDs")
    elif type(metadata["turn_index"]) is not int or metadata["turn_index"] < 1:
        raise ValueError("invalid turn index")
    elif metadata["recall_guard_authoritative"] is not False:
        raise ValueError("recall metadata cannot grant authority")

    await ensure_erasure_constraints(graphiti)
    result = await graphiti.driver.execute_query(
        LOCK_ERASURE_CONTROL + """ WHERE coalesce(gate.revision,0)=$revision
        MATCH (e:Episodic {uuid:$uuid})
        WHERE e.group_id=$group_id AND e.source_description='chat'
          AND coalesce(e.deleted,false)=false
          AND coalesce(e.erasure_quarantined,false)=false
          AND coalesce(e.chat_finalized,false)=false
          AND (e.origin_class IS NULL OR e.origin_class='agent_derived')
          AND NOT EXISTS { MATCH (:FractalErasure {target_uuid:e.uuid}) }
          AND NOT EXISTS { MATCH (:User)-[:AUTHORED]->(e) }
          AND NOT EXISTS { MATCH (e)-[:STORED_FOR]->(:User) }
        WITH collect(e) AS matches
        WHERE size(matches)=1
        WITH matches[0] AS e
        OPTIONAL MATCH (source:Episodic)
        WHERE source.uuid IN $sources
          AND source.group_id=$group_id
          AND source.episode_kind='chat_turn'
          AND source.conversation_id=$conversation_id
          AND source.chat_finalized=true AND source.chat_erasure_revision <= $revision
          AND """ + visible_node("source") + """
          AND EXISTS { MATCH (source)-[:STORED_FOR]->(:User {user_id:$user_id}) }
        WITH e,collect(source) AS source_nodes
        WHERE size(source_nodes)=size($sources)
          AND all(id IN $sources WHERE single(n IN source_nodes WHERE n.uuid=id))
        SET e += $metadata,
            e.origin_class='agent_derived',
            e.authoritative_fact=false,
            e.chat_erasure_revision=$revision,
            e.chat_finalized=true
        MERGE (u:User {user_id:$user_id})
        MERGE (e)-[:STORED_FOR]->(u)
        FOREACH (source IN source_nodes |
            SET source.summarized=true, source.summary_uuid=e.uuid)
        WITH e
        OPTIONAL MATCH (e)-[:MENTIONS]->(n:Entity)
        FOREACH (_ IN CASE WHEN n IS NULL THEN [] ELSE [1] END |
            SET n.has_non_owner_source=true)
        RETURN DISTINCT e.uuid AS uuid
        """,
        uuid=episode_uuid, user_id=user_id, group_id=group_id,
        revision=revision, metadata=metadata,
        conversation_id=metadata["conversation_id"], sources=sources,
    )
    if len(result.records) != 1 or result.records[0].get("uuid") != episode_uuid:
        raise ErasureFenceChanged("chat finalization not admitted at original erasure state")
