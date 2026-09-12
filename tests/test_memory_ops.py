"""Tests for the current MemoryOps layer contract."""

from unittest.mock import AsyncMock, Mock

import pytest

from core.memory_ops import ContextResult, MemoryOps, SearchResult, _recent_memories


@pytest.fixture
def mock_graphiti():
    """Mock Graphiti instance for deterministic MemoryOps tests."""
    graphiti = Mock()
    graphiti.search_ = AsyncMock()
    graphiti.driver = None
    graphiti._driver = None
    return graphiti


@pytest.fixture
def memory_ops(mock_graphiti):
    """MemoryOps instance isolated from process-global recent-memory state."""
    user_id = "memory_ops_contract_user"
    _recent_memories.pop(user_id, None)
    ops = MemoryOps(mock_graphiti, user_id)
    yield ops
    _recent_memories.pop(user_id, None)


class DummySearchResults:
    def __init__(
        self,
        *,
        episodes=None,
        nodes=None,
        edges=None,
        communities=None,
        episode_reranker_scores=None,
        node_reranker_scores=None,
        edge_reranker_scores=None,
        community_reranker_scores=None,
    ):
        self.episodes = episodes or []
        self.nodes = nodes or []
        self.edges = edges or []
        self.communities = communities or []
        self.episode_reranker_scores = episode_reranker_scores or []
        self.node_reranker_scores = node_reranker_scores or []
        self.edge_reranker_scores = edge_reranker_scores or []
        self.community_reranker_scores = community_reranker_scores or []


class DummyEpisode:
    def __init__(
        self,
        *,
        uuid="ep1",
        content="episode content",
        group_id="personal",
        source_description="test",
        episode_kind="",
    ):
        self.uuid = uuid
        self.content = content
        self.group_id = group_id
        self.source_description = source_description
        self.episode_kind = episode_kind
        self.created_at = None


class DummyNode:
    def __init__(self, *, uuid="n1", name="Entity", summary="Summary", group_id="personal"):
        self.uuid = uuid
        self.name = name
        self.summary = summary
        self.group_id = group_id


class DummyEdge:
    def __init__(
        self,
        *,
        uuid="e1",
        subject="A",
        object="B",
        relationship_type="RELATES_TO",
        fact="A relates to B",
        name=None,
        group_id="personal",
    ):
        self.uuid = uuid
        self.subject = subject
        self.object = object
        self.relationship_type = relationship_type
        self.fact = fact
        self.name = name
        self.group_id = group_id


class TestMemoryOps:
    """Deterministic coverage for active MemoryOps behavior."""

    @pytest.mark.asyncio
    async def test_remember_text_routes_to_canonical_ingest_pipeline(self, memory_ops):
        memory_ops.ingest_pipeline = AsyncMock(return_value={"status": "ok", "added": 1})

        result = await memory_ops.remember_text("test text", memory_type="personal")

        assert result == {"status": "ok", "added": 1}
        memory_ops.ingest_pipeline.assert_awaited_once_with(
            "test text",
            source_description="memory_ops",
            memory_type="personal",
        )

    @pytest.mark.asyncio
    async def test_search_memory_combines_results(self, memory_ops, mock_graphiti):
        mock_graphiti.search_.return_value = DummySearchResults(
            episodes=[DummyEpisode(uuid="ep1", content="episode content with enough length")],
            nodes=[DummyNode(uuid="ent1", name="Entity Name", summary="Entity summary")],
            edges=[DummyEdge(uuid="edge1")],
            communities=[],
            episode_reranker_scores=[0.8],
            node_reranker_scores=[0.7],
            edge_reranker_scores=[0.6],
        )

        result = await memory_ops.search_memory("test query")

        assert isinstance(result, SearchResult)
        assert result.total_episodes == 1
        assert result.total_entities == 1
        assert result.total_edges == 1
        assert len(result.episodes) == 1
        assert len(result.entities) == 1

    @pytest.mark.asyncio
    async def test_build_context_formats_current_episode_section(self, memory_ops, mock_graphiti):
        mock_graphiti.search_.return_value = DummySearchResults(
            episodes=[DummyEpisode(uuid="ep1", content="Test episode content with enough length")],
            nodes=[],
            edges=[],
            communities=[],
            episode_reranker_scores=[0.9],
        )

        result = await memory_ops.build_context_for_query("test query")

        assert isinstance(result, ContextResult)
        assert "## Эпизоды" in result.text
        assert "Test episode content" in result.text
        assert result.sources["episodes"] == 1
        assert result.sources["entities"] == 0
        assert result.source_ids == ["ep1"]

    @pytest.mark.asyncio
    async def test_unsummarized_relevant_chat_turn_survives_ram_buffer_loss(self, memory_ops, mock_graphiti):
        """Persisted pre-summary chat must remain expressible after RAM context is gone."""
        prior_turn = "User: Remember the cobalt launch key\nAssistant: The key is ORBIT-7"
        mock_graphiti.search_.return_value = DummySearchResults(
            episodes=[
                DummyEpisode(
                    uuid="chat-turn-1",
                    content=prior_turn,
                    source_description="chat",
                    episode_kind="chat_turn",
                )
            ],
            nodes=[],
            edges=[],
            communities=[],
            episode_reranker_scores=[1.0],
        )

        result = await memory_ops.build_context_for_query("What was the cobalt launch key?")

        assert "ORBIT-7" in result.text
        assert "chat-turn-1" in result.source_ids
        assert result.sources["episodes"] == 1

    @pytest.mark.asyncio
    async def test_context_truncation(self, memory_ops, mock_graphiti):
        long_content = "Very long content " * 1000
        mock_graphiti.search_.return_value = DummySearchResults(
            episodes=[DummyEpisode(uuid="ep1", content=long_content)],
            nodes=[],
            edges=[],
            communities=[],
            episode_reranker_scores=[0.9],
        )

        result = await memory_ops.build_context_for_query("test", max_tokens=100)

        assert len(result.text) < len(long_content)
        assert "[Контекст обрезан" in result.text
        assert result.token_estimate <= 100
