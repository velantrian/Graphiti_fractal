from unittest.mock import AsyncMock, Mock, patch

import pytest

from knowledge.ingest import ingest_text_document


@pytest.fixture
def mock_graphiti():
    g = Mock()
    ep_mock = Mock()
    ep_mock.uuid = "test-uuid"
    ep_mock.episode.uuid = "test-uuid"
    ep_mock.extracted_entities = []
    ep_mock.extracted_edges = []

    g.add_episode = AsyncMock(return_value=ep_mock)
    g.driver = Mock()
    g.driver.execute_query = AsyncMock(return_value=Mock(records=[]))
    return g


@pytest.fixture
def mock_jobs():
    with patch("api.jobs.update_upload_job") as mock:
        yield mock


@pytest.fixture
def successful_ingest_claims():
    """Keep this unit test on the app contract, not live Neo4j claim storage."""
    with (
        patch(
            "knowledge.ingest.acquire_ingest_claim",
            new=AsyncMock(return_value="test-claim-token"),
        ) as acquire,
        patch(
            "knowledge.ingest.mark_ingest_claim_episode_created",
            new=AsyncMock(),
        ) as mark_created,
        patch(
            "knowledge.ingest.finalize_episode_identity",
            new=AsyncMock(),
        ) as finalize,
        patch(
            "knowledge.ingest.get_embedding",
            new=AsyncMock(return_value=None),
        ),
    ):
        yield acquire, mark_created, finalize


@pytest.mark.asyncio
async def test_ingest_chunks_text(mock_graphiti, mock_jobs, successful_ingest_claims):
    long_text = "A" * 1600 + " " + "B" * 400

    result = await ingest_text_document(
        mock_graphiti,
        long_text,
        source_description="test_doc",
        user_id="user1",
        job_id="job123",
    )

    assert result["status"] == "ok"
    assert result["chunks"] >= 2
    assert result["added"] >= 2
    assert mock_graphiti.add_episode.call_count >= 2

    acquire, mark_created, finalize = successful_ingest_claims
    assert acquire.await_count == result["added"]
    assert mark_created.await_count == result["added"]
    assert finalize.await_count == result["added"]

    assert mock_jobs.call_count >= 3
    calls = mock_jobs.call_args_list
    assert calls[0].kwargs["processed_chunks"] == 0
    assert calls[0].kwargs["stage"] == "ingest"
    assert calls[-1].kwargs["stage"] == "done"
    assert calls[-1].kwargs["processed_chunks"] == result["chunks"]


@pytest.mark.asyncio
async def test_ingest_short_text(mock_graphiti, mock_jobs, successful_ingest_claims):
    result = await ingest_text_document(
        mock_graphiti,
        "Short text",
        source_description="short_doc",
    )

    assert result["status"] == "ok"
    assert result["chunks"] == 1
    assert result["added"] == 1
    mock_graphiti.add_episode.assert_awaited_once()

    acquire, mark_created, finalize = successful_ingest_claims
    acquire.assert_awaited_once()
    mark_created.assert_awaited_once()
    finalize.assert_awaited_once()
