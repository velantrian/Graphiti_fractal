import asyncio
from types import SimpleNamespace

from core.memory_ops import MemoryOps


class FakeGraphiti:
    def __init__(self):
        self.calls = []

    async def search_(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            episodes=[SimpleNamespace(uuid="ep-1", content="local episode", group_id=kwargs["group_ids"][0], episode_kind="document", source_description="doc", created_at=None)],
            episode_reranker_scores=[0.8],
            nodes=[SimpleNamespace(uuid="node-1", name="Entity", summary="entity summary", group_id=kwargs["group_ids"][0])],
            node_reranker_scores=[0.9],
            edges=[SimpleNamespace(uuid="edge-1", fact="A relates to B", subject="A", object="B", relationship_type="REL", group_id=kwargs["group_ids"][0])],
            edge_reranker_scores=[0.7],
            communities=[SimpleNamespace(uuid="community-1", name="Theme", summary="global theme", group_id=kwargs["group_ids"][0])],
            community_reranker_scores=[0.6],
        )


def run(coro):
    return asyncio.run(coro)


def test_local_uses_canonical_search_and_excludes_communities():
    graphiti = FakeGraphiti()
    memory = MemoryOps(graphiti, "owner")
    result = run(memory.search_memory("What happened to Entity?", scopes=["knowledge"], retrieval_mode="local"))
    assert len(graphiti.calls) == 1
    assert result.entities and result.edges and result.episodes
    assert result.communities == []


def test_global_uses_same_search_but_returns_only_communities():
    graphiti = FakeGraphiti()
    memory = MemoryOps(graphiti, "owner")
    result = run(memory.search_memory("What are the main themes across the whole corpus?", scopes=["knowledge"], retrieval_mode="global"))
    assert len(graphiti.calls) == 1
    assert result.communities
    assert result.entities == []
    assert result.edges == []
    assert result.episodes == []


def test_drift_combines_local_and_community_context_without_second_search():
    graphiti = FakeGraphiti()
    memory = MemoryOps(graphiti, "owner")
    result = run(memory.search_memory("How does Entity connect to the broader pattern?", scopes=["knowledge"], retrieval_mode="drift"))
    assert len(graphiti.calls) == 1
    assert result.communities and result.entities and result.edges and result.episodes
    assert result.entities[0]["score"] == 0.9 * 0.55
    assert result.communities[0]["score"] == 0.6 * 0.45


def test_auto_routes_global_query_to_community_only_context():
    graphiti = FakeGraphiti()
    memory = MemoryOps(graphiti, "owner")
    result = run(memory.search_memory("Какие общие темы по всему корпусу?", scopes=["knowledge"], retrieval_mode="auto"))
    assert result.communities
    assert not result.entities


def test_invalid_mode_fails_before_graph_search():
    graphiti = FakeGraphiti()
    memory = MemoryOps(graphiti, "owner")
    try:
        run(memory.search_memory("query", scopes=["knowledge"], retrieval_mode="mystery"))
    except ValueError:
        pass
    else:
        raise AssertionError("invalid mode must fail closed")
    assert graphiti.calls == []
