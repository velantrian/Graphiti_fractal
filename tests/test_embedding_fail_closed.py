import pytest

from core.graphiti_client import CustomEmbedder


@pytest.mark.asyncio
async def test_embedder_never_fabricates_zero_vector(monkeypatch):
    async def no_embedding(_text):
        return None

    monkeypatch.setattr("core.graphiti_client.get_embedding", no_embedding)
    embedder = CustomEmbedder()

    with pytest.raises(RuntimeError, match="embedding service unavailable"):
        await embedder.create("hello")


@pytest.mark.asyncio
async def test_embedder_rejects_dimension_mismatch(monkeypatch):
    vectors = {"a": [1.0, 2.0], "b": [1.0, 2.0, 3.0]}

    async def fake_embedding(text):
        return vectors[text]

    monkeypatch.setattr("core.graphiti_client.get_embedding", fake_embedding)
    embedder = CustomEmbedder()

    with pytest.raises(RuntimeError, match="dimension mismatch"):
        await embedder.create(["a", "b"])
