"""Provider-free guard tests; actual Cypher runs in Neo4j integration."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.erasure_guard import admit_search_candidates, read_erasure_state, erase_node
from core.memory_ops import MemoryOps


@pytest.mark.asyncio
async def test_admission_cannot_invent_or_expand_candidates():
    driver = SimpleNamespace(execute_query=AsyncMock(return_value=SimpleNamespace(records=[
        {"kind":"episode","uuid":"kept","group_id":"knowledge"},
        {"kind":"episode","uuid":"other","group_id":"knowledge"},
    ])))
    graphiti = SimpleNamespace(driver=driver)
    result = await admit_search_candidates(graphiti, [
        {"type":"episode","uuid":"kept","group_id":"knowledge"},
        {"type":"episode","uuid":"not-admitted","group_id":"knowledge"},
    ])
    assert result == {("episode","kept","knowledge")}


@pytest.mark.asyncio
async def test_retrieval_fails_closed_if_database_admission_unavailable():
    graphiti = SimpleNamespace(
        driver=SimpleNamespace(execute_query=AsyncMock(side_effect=RuntimeError("db unavailable"))),
        search_=AsyncMock(return_value=SimpleNamespace(
            episodes=[SimpleNamespace(uuid="stale",content="must not return",group_id="knowledge")],
        )),
    )
    with pytest.raises(RuntimeError, match="db unavailable"):
        await MemoryOps(graphiti,"owner").search_memory("query",scopes=["knowledge"])


@pytest.mark.asyncio
async def test_erasure_state_requires_valid_database_observation():
    driver = SimpleNamespace(execute_query=AsyncMock(return_value=SimpleNamespace(records=[])))
    with pytest.raises(RuntimeError, match="unavailable"):
        await read_erasure_state(SimpleNamespace(driver=driver))


@pytest.mark.asyncio
async def test_shared_delete_does_not_claim_success_without_target():
    driver = SimpleNamespace(execute_query=AsyncMock(return_value=SimpleNamespace(records=[])))
    result = await erase_node(SimpleNamespace(driver=driver),uuid="missing",hard=True)
    assert result["deleted"] is False
    assert result["erasure_revision"] is None
