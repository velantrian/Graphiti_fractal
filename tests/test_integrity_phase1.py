"""Provider-free regressions for integrity phase 1."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.jobs import create_upload_job, delete_upload_job, finish_upload_job, get_upload_job
from core.memory_ops import MemoryOps
from knowledge.ingest import episode_exists, UnconfirmedEpisodeIdentity, _completeness
from experience.retrieval import get_success_patterns, get_antipatterns
from mcp_server import server


@pytest.mark.asyncio
@pytest.mark.parametrize("records,expected", [
    ([], False),
    ([{"uuid": "episode", "confirmed": True}], True),
    ([{"uuid": "orphan", "confirmed": False}], None),
])
async def test_existing_episode_requires_confirmed_identity(records, expected):
    driver = SimpleNamespace(execute_query=AsyncMock(return_value=SimpleNamespace(records=records)))
    graphiti = SimpleNamespace(driver=driver)
    if expected is None:
        with pytest.raises(UnconfirmedEpisodeIdentity):
            await episode_exists(graphiti, "fp", "body", "project", origin_class="owner", user_id="owner")
    else:
        assert await episode_exists(graphiti, "fp", "body", "project", origin_class="owner", user_id="owner") is expected
    query = driver.execute_query.await_args.args[0]
    assert "c.state = 'COMMITTED'" in query
    assert "c.episode_uuid = e.uuid" in query
    assert ":AUTHORED" in query


def test_unconfirmed_existing_is_not_success():
    assert _completeness(1, 0, 0, 0, 0, 0, 1) == "unconfirmed"


@pytest.mark.asyncio
async def test_mcp_cannot_claim_owner_origin(monkeypatch):
    monkeypatch.setattr(server, "_get_graphiti", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(server, "get_instance_user_id", lambda: "owner")
    remember = AsyncMock(return_value={"completeness": "complete"})
    upload = AsyncMock(return_value={"completeness": "complete"})
    monkeypatch.setattr(MemoryOps, "remember_text", remember)
    monkeypatch.setattr(server, "ingest_text_document", upload)
    await server._tool_call("memory.remember", {"text": "agent text", "origin_class": "owner"})
    await server._tool_call("memory.upload", {"text": "agent text", "origin_class": "owner"})
    assert remember.await_args.kwargs["origin_class"] == "agent_derived"
    assert upload.await_args.kwargs["origin_class"] == "agent_derived"


@pytest.mark.asyncio
async def test_recall_does_not_invent_source_id():
    graphiti = SimpleNamespace(search_=AsyncMock(return_value=SimpleNamespace(
        episodes=[SimpleNamespace(content="unidentified episode", uuid=None)],
        nodes=[SimpleNamespace(name="unidentified entity")],
        edges=[SimpleNamespace(fact="unidentified relation", uuid="")],
        communities=[SimpleNamespace(summary="unidentified community", uuid=None)],
    )))
    result = await MemoryOps(graphiti, "owner").build_context_for_query("query", scopes=["knowledge"])
    assert result.source_ids == []
    assert result.text == ""


@pytest.mark.asyncio
async def test_experience_queries_exclude_deleted_runs():
    driver = SimpleNamespace(execute_query=AsyncMock(return_value=SimpleNamespace(records=[])))
    graphiti = SimpleNamespace(driver=driver)
    await get_success_patterns(graphiti, task_type=None, context_hash=None)
    await get_antipatterns(graphiti, task_type=None, context_hash=None)
    assert driver.execute_query.await_count == 2
    for call in driver.execute_query.await_args_list:
        assert "coalesce(tr.deleted, false) = false" in call.args[0]


@pytest.mark.parametrize("result,stage,completeness", [
    ({"status": "ok", "completeness": "complete"}, "done", "complete"),
    ({"status": "ok", "completeness": "partial"}, "done_with_warnings", "partial"),
    ({"status": "ok", "completeness": "unconfirmed"}, "done_with_warnings", "unconfirmed"),
    ({"status": "error", "completeness": "failed"}, "error", "failed"),
    ({"status": "ok"}, "done_with_warnings", "unknown"),
])
def test_upload_stage_never_confirms_incomplete_ingest(result, stage, completeness):
    job_id = create_upload_job()
    try:
        assert finish_upload_job(job_id, result)
        assert get_upload_job(job_id)["stage"] == stage
        assert get_upload_job(job_id)["completeness"] == completeness
    finally:
        delete_upload_job(job_id)
