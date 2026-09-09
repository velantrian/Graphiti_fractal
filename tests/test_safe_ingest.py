from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import BaseModel, ValidationError

from core.safe_graphiti import filter_graphiti_results
from knowledge.ingest import ingest_text_document


def test_filter_graphiti_results_with_malformed_data():
    class MockEntity:
        def __init__(self, uuid, name, summary=""):
            self.uuid = uuid
            self.name = name
            self.summary = summary
            self.node_type = "Entity"

    class MockEdge:
        def __init__(self, source, target, rel_type):
            self.source_node_uuid = source
            self.target_node_uuid = target
            self.relationship_type = rel_type

    mock_results = Mock()
    mock_results.extracted_entities = [
        MockEntity("uuid1", "Valid Entity"),
        MockEntity(None, "Invalid - No UUID"),
        MockEntity("uuid2", None),
        None,
    ]
    mock_results.extracted_edges = [
        MockEdge("uuid1", "uuid2", "RELATES_TO"),
        MockEdge(None, "uuid2", "NO_SOURCE"),
        None,
    ]

    filtered = filter_graphiti_results(mock_results)

    assert len(filtered["entities"]) == 1
    assert filtered["entities"][0]["uuid"] == "uuid1"
    assert filtered["dropped_entities"] == 3
    assert len(filtered["edges"]) == 1
    assert filtered["edges"][0]["relationship_type"] == "RELATES_TO"
    assert filtered["dropped_edges"] == 2


@pytest.mark.asyncio
async def test_ingest_validation_failure_is_fail_closed():
    """Malformed provider output must not be recovered into a successful ingest."""

    class DummyModel(BaseModel):
        x: int

    with pytest.raises(ValidationError) as exc_info:
        DummyModel(x="not an int")

    graphiti = Mock()
    graphiti.add_episode = AsyncMock(side_effect=exc_info.value)
    graphiti.driver = Mock()

    with (
        patch("knowledge.ingest.episode_exists", new=AsyncMock(return_value=False)),
        patch(
            "knowledge.ingest.acquire_ingest_claim",
            new=AsyncMock(return_value="test-claim-token"),
        ),
        patch("knowledge.ingest.release_ingest_claim", new=AsyncMock()) as release_claim,
        patch(
            "knowledge.ingest.mark_ingest_claim_episode_created",
            new=AsyncMock(),
        ) as mark_created,
        patch(
            "knowledge.ingest.finalize_episode_identity",
            new=AsyncMock(),
        ) as finalize,
    ):
        result = await ingest_text_document(
            graphiti,
            "Test content",
            source_description="Test Source",
            user_id="test-user",
        )

    assert result["status"] == "error"
    assert result["added"] == 0
    assert result["skipped"] == 0
    assert any("ValidationError" in warning for warning in result["warnings"])
    release_claim.assert_awaited_once()
    mark_created.assert_not_awaited()
    finalize.assert_not_awaited()
