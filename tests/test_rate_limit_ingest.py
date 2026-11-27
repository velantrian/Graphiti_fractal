from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest
from tests.helpers.fence import mock_ingest_revision  # noqa: F401

from api.jobs import _upload_jobs as UPLOAD_JOBS
from core.rate_limit_retry import with_rate_limit_retry
from knowledge.ingest import ingest_text_document


def _fake_execute_query(query, **kwargs):
    # episode_exists()'s dedup lookup must see no existing episode so ingestion
    # proceeds; every other query in the atomic-claim path (constraint setup,
    # claim CREATE, claim finalization) only needs a truthy record.
    if "e.fingerprint = $fp OR e.content = $content" in query:
        return MagicMock(records=[])
    return MagicMock(records=[{"token": "test-token", "uuid": "test-uuid", "attempts": 1}])


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


@pytest.fixture(autouse=True)
def _disable_retry_wall_clock():
    """Keep retry contracts deterministic and free of real backoff sleeps."""
    with (
        patch("core.rate_limit_retry.asyncio.sleep", new=AsyncMock()) as sleep,
        patch("core.rate_limit_retry.random.random", return_value=0.0),
    ):
        yield sleep


@pytest.mark.asyncio
async def test_retry_wrapper_logic(_disable_retry_wall_clock):
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
    assert _disable_retry_wall_clock.await_count == 2
    args, _ = callback.call_args
    assert args[0] == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_rate_limit_from_add_episode_is_not_retried_and_is_unconfirmed(_disable_retry_wall_clock):
    """Graphiti may have stored the episode before the error surfaced: a second
    add_episode could duplicate it, so the outcome is unconfirmed, not retried."""
    graphiti = MockGraphiti()
    error_429 = openai.RateLimitError(
        message="Rate limit reached. Please try again in 0.1s.",
        response=MagicMock(),
        body=None,
    )
    graphiti.add_episode.side_effect = [error_429, {"uuid": "123", "name": "Success"}]

    job_id = "test_job_retry"
    UPLOAD_JOBS[job_id] = {"status": "pending", "stage": "starting", "timing": {}}

    with (
        patch("api.jobs.update_upload_job") as mock_update,
        patch("knowledge.ingest.get_embedding", new=AsyncMock(return_value=None)),
        patch("knowledge.ingest.mark_ingest_claim_write_attempt", new=AsyncMock(return_value=1)),
    ):
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

    assert result["added"] == 0
    assert result["completeness"] == "unconfirmed"
    assert result["unconfirmed_writes"] == 1
    assert graphiti.add_episode.call_count == 1
    assert _disable_retry_wall_clock.await_count == 0
    assert not [c for c in mock_update.mock_calls if c.kwargs.get("stage") == "rate_limited"]
