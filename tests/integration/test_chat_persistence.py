"""Opt-in integration tests for persisted chat turns, summaries, and temporal search.

Run with a real Neo4j/OpenAI test environment:
    RUN_LLM_INGEST_TESTS=1 pytest -q tests/integration/test_chat_persistence.py
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest

from core.memory_ops import MemoryOps
from core.task_registry import drain
from simple_chat_agent import SimpleChatAgent

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LLM_INGEST_TESTS") != "1",
    reason="Requires real LLM + Graphiti ingest; set RUN_LLM_INGEST_TESTS=1",
)


async def _count_turns(driver, *, user_id: str, marker: str) -> int:
    result = await driver.execute_query(
        """
        MATCH (:User {user_id:$user_id})-[:AUTHORED]->(e:Episodic {episode_kind:'chat_turn'})
        WHERE e.content CONTAINS $marker
        RETURN count(e) AS count
        """,
        user_id=user_id,
        marker=marker,
    )
    return int(result.records[0]["count"]) if result.records else 0


@pytest.mark.asyncio
async def test_single_chat_request_persists_exactly_one_turn(graphiti_client, llm_client):
    graphiti = graphiti_client
    user_id = f"chat_no_dup_{datetime.now(timezone.utc).timestamp()}"
    marker = f"DUPLICATE_TEST_{datetime.now(timezone.utc).isoformat()}"

    agent = SimpleChatAgent(llm_client, MemoryOps(graphiti, user_id))
    await agent.answer_core(f"Test message: {marker}")
    await drain(timeout=60)

    assert await _count_turns(graphiti.driver, user_id=user_id, marker=marker) == 1


@pytest.mark.asyncio
async def test_concurrent_chat_turn_indices_are_unique_and_sequential(graphiti_client, llm_client):
    graphiti = graphiti_client
    user_id = f"chat_race_{datetime.now(timezone.utc).timestamp()}"
    marker_prefix = f"RACE_TEST_{datetime.now(timezone.utc).isoformat()}"
    memory = MemoryOps(graphiti, user_id)

    async def send(index: int):
        marker = f"{marker_prefix}_MSG_{index}"
        return await SimpleChatAgent(llm_client, memory).answer_core(
            f"Concurrent message {index}: {marker}"
        )

    await asyncio.gather(*(send(index) for index in range(5)))
    await drain(timeout=90)

    result = await graphiti.driver.execute_query(
        """
        MATCH (:User {user_id:$user_id})-[:AUTHORED]->(e:Episodic {episode_kind:'chat_turn'})
        WHERE e.content CONTAINS $marker_prefix
        RETURN e.turn_index AS turn_index, e.conversation_id AS conversation_id
        ORDER BY e.turn_index ASC
        """,
        user_id=user_id,
        marker_prefix=marker_prefix,
    )
    indices = [int(record["turn_index"]) for record in result.records]
    conversation_ids = {record["conversation_id"] for record in result.records}

    assert len(conversation_ids) == 1
    assert indices == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_summary_references_real_persisted_turn_uuids(graphiti_client, llm_client):
    graphiti = graphiti_client
    user_id = f"chat_summary_{datetime.now(timezone.utc).timestamp()}"
    marker_prefix = f"SUMMARY_TEST_{datetime.now(timezone.utc).isoformat()}"
    memory = MemoryOps(graphiti, user_id)
    agent = SimpleChatAgent(llm_client, memory)

    for index in range(10):
        await agent.answer_core(f"Message {index}: {marker_prefix}_MSG_{index}")

    await drain(timeout=180)

    summary_result = await graphiti.driver.execute_query(
        """
        MATCH (:User {user_id:$user_id})-[:AUTHORED]->(s:Episodic {episode_kind:'chat_summary'})
        RETURN s.uuid AS uuid,
               s.conversation_id AS conversation_id,
               s.covers_turns AS covers_turns,
               s.summarized_turns AS summarized_turns
        ORDER BY s.created_at DESC
        LIMIT 1
        """,
        user_id=user_id,
    )
    assert summary_result.records, "expected one persisted chat summary"
    summary = summary_result.records[0]
    source_uuids = list(summary["summarized_turns"] or [])

    assert summary["covers_turns"] == "1-10"
    assert len(source_uuids) == 10
    assert len(set(source_uuids)) == 10

    source_result = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic)
        WHERE e.uuid IN $source_uuids
        RETURN e.uuid AS uuid,
               e.turn_index AS turn_index,
               e.summarized AS summarized,
               e.summary_uuid AS summary_uuid,
               e.content AS content
        ORDER BY e.turn_index ASC
        """,
        source_uuids=source_uuids,
    )
    assert len(source_result.records) == 10
    assert [int(record["turn_index"]) for record in source_result.records] == list(range(1, 11))
    assert all(record["summarized"] is True for record in source_result.records)
    assert all(record["summary_uuid"] == summary["uuid"] for record in source_result.records)
    assert all(marker_prefix in (record["content"] or "") for record in source_result.records)


@pytest.mark.asyncio
async def test_temporal_search_current_and_as_of(graphiti_client):
    graphiti = graphiti_client
    user_id = f"temporal_{datetime.now(timezone.utc).timestamp()}"
    group_id = f"temporal_group_{datetime.now(timezone.utc).timestamp()}"
    memory = MemoryOps(graphiti, user_id)

    now = datetime.now(timezone.utc)
    first_time = now - timedelta(days=30)
    second_time = now

    await graphiti.add_episode(
        name="temporal_test_moscow",
        episode_body="Сергей жил в Москве.",
        source_description="temporal_test",
        group_id=group_id,
        reference_time=first_time,
    )
    await graphiti.add_episode(
        name="temporal_test_petersburg",
        episode_body="Сергей переехал в Санкт-Петербург и теперь живет в Санкт-Петербурге.",
        source_description="temporal_test",
        group_id=group_id,
        reference_time=second_time,
    )

    try:
        current = await memory.search_memory(
            "где живет Сергей",
            scopes=[group_id],
            limit=10,
        )
        current_facts = [edge.get("fact", "") for edge in current.edges]
        assert any("Санкт-Петербург" in fact for fact in current_facts)
        assert not any("Москв" in fact for fact in current_facts)

        historical = await memory.search_memory(
            "где жил Сергей",
            scopes=[group_id],
            limit=10,
            as_of=now - timedelta(days=15),
        )
        historical_facts = [edge.get("fact", "") for edge in historical.edges]
        assert any("Москв" in fact for fact in historical_facts)
    finally:
        await graphiti.driver.execute_query(
            "MATCH (n {group_id:$group_id}) DETACH DELETE n",
            group_id=group_id,
        )
        await graphiti.driver.execute_query(
            "MATCH (u:User {user_id:$user_id}) DETACH DELETE u",
            user_id=user_id,
        )
