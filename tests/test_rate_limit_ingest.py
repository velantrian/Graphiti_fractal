import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest

from api.jobs import _upload_jobs as UPLOAD_JOBS
from core.rate_limit_retry import with_rate_limit_retry
from knowledge.ingest import ingest_text_document


def _fake_execute_query(query, **kwargs):
    # episode_exists()'s dedup lookup must see no existing episode so ingestion
    # proceeds; every other query in the atomic-claim path (constraint setup,
    # claim CREATE, claim finalization) only needs a truthy record.
    if "e.fingerprint = $fp OR e.content = $content" in query:
        return MagicMock(records=[])
    return MagicMock(records=[{"token": "test-token", "uuid": "test-uuid"}])


class MockGraphiti:
    def __init__(self):
        self.driver = MagicMock()
        self.driver.execute_query = AsyncMock(side_effect=_fake_execute_query)
        self.add_episode = AsyncMock()


@pytest.fixture(autouse=True)
def _clear_upload_jobs():
    UPLOAD_JOBS.clear()
    yield
    UPLOAD_JOBS.clear()


@pytest.mark.asyncio
async def test_retry_wrapper_logic():
    """Test the retry wrapper in isolation."""
    mock_op = AsyncMock()
    error = openai.RateLimitError(
        message="Please try again in 0.1s.",
        response=MagicMock(),
        body=None,
    )
    mock_op.side_effect = [error, error, "success"]

    callback = MagicMock()

    result = await with_rate_limit_retry(
        lambda: mock_op(),
        op_name="test_op",
        max_attempts=5,
        base_sleep=0.1,
        on_rate_limit=callback,
    )

    assert result == "success"
    assert mock_op.call_count == 3
    assert callback.call_count == 2
    args, _ = callback.call_args
    assert args[0] >= 0.6


@pytest.mark.asyncio
async def test_ingest_flow_with_retry():
    """Test the ingest flow with mocked Graphiti and job-status updates."""
    graphiti = MockGraphiti()
    error_429 = openai.RateLimitError(
        message="Rate limit reached. Please try again in 0.1s.",
        response=MagicMock(),
        body=None,
    )
    graphiti.add_episode.side_effect = [error_429, error_429, {"uuid": "123", "name": "Success"}]

    job_id = "test_job_retry"
    UPLOAD_JOBS[job_id] = {
        "status": "pending",
        "stage": "starting",
        "timing": {},
    }

    # ingest_text_document imports update_upload_job locally from api.jobs
    # (not the api-package re-export), so the patch target must match that.
    with patch("api.jobs.update_upload_job") as mock_update:
        def side_effect_update(jid, **kwargs):
            if jid in UPLOAD_JOBS:
                UPLOAD_JOBS[jid].update(kwargs)

        mock_update.side_effect = side_effect_update

        result = await ingest_text_document(
            graphiti,
            "Test content for retry",
            job_id=job_id,
            source_description="retry_test",
        )

        assert result["status"] == "ok"
        assert graphiti.add_episode.call_count == 3

        rate_limit_calls = [
            call
            for call in mock_update.mock_calls
            if "stage" in call.kwargs and call.kwargs["stage"] == "rate_limited"
        ]
        assert len(rate_limit_calls) == 2
        assert rate_limit_calls[0].kwargs["attempt"] == 1
        assert rate_limit_calls[1].kwargs["attempt"] == 2
