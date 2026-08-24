import asyncio
import logging
import os

from dotenv import load_dotenv
from graphiti_core import Graphiti
from graphiti_core.embedder.client import EmbedderClient
from neo4j.exceptions import ClientError

from core.config import get_config
from core.embeddings import get_embedding
from core.migrations import apply_migrations

logger = logging.getLogger(__name__)
load_dotenv()


class CustomEmbedder(EmbedderClient):
    """Graphiti embedder backed by the project cache.

    Embedding failures are fail-closed: a zero vector is never fabricated because
    it would create valid-looking but semantically meaningless vector entries.
    """

    async def create(self, input_data):
        if isinstance(input_data, str):
            result = await get_embedding(input_data)
            if result is None:
                raise RuntimeError("embedding service unavailable for string input")
            return result

        if isinstance(input_data, list) and all(isinstance(item, str) for item in input_data):
            vectors = []
            for text in input_data:
                vector = await get_embedding(text)
                if vector is None:
                    raise RuntimeError("embedding service unavailable for list input")
                vectors.append(vector)
            if not vectors:
                raise ValueError("embedding input list is empty")
            dimensions = {len(vector) for vector in vectors}
            if len(dimensions) != 1:
                raise RuntimeError("embedding dimension mismatch")
            return [sum(values) / len(values) for values in zip(*vectors)]

        raise TypeError(f"unsupported embedding input type: {type(input_data).__name__}")

    async def create_batch(self, input_data_list):
        return [await self.create(text) for text in input_data_list]


class GraphitiClient:
    """Lazy Graphiti wrapper with idempotent schema initialization."""

    def __init__(self, uri: str, user: str, password: str):
        self._graphiti = Graphiti(
            uri=uri,
            user=user,
            password=password,
            embedder=CustomEmbedder(),
        )
        self._ready = False
        self._lock = asyncio.Lock()

    async def ensure_ready(self) -> Graphiti:
        if self._ready:
            return self._graphiti

        async with self._lock:
            if not self._ready:
                try:
                    await self._graphiti.build_indices_and_constraints()
                except ClientError as exc:
                    if "EquivalentSchemaRuleAlreadyExists" not in str(exc):
                        raise
                await apply_migrations(self._graphiti)
                self._ready = True
        return self._graphiti

    @property
    def raw(self) -> Graphiti:
        return self._graphiti


_graphiti_singleton: GraphitiClient | None = None
_write_semaphores: dict[int, asyncio.Semaphore] = {}


def reset_graphiti_client() -> None:
    global _graphiti_singleton
    _graphiti_singleton = None
    _write_semaphores.clear()


def get_graphiti_client(*, force_new: bool = False) -> GraphitiClient:
    global _graphiti_singleton

    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")
    if not all([uri, user, password]):
        raise RuntimeError("ENV vars NEO4J_URI/USER/PASSWORD are required")

    if force_new or _graphiti_singleton is None:
        _graphiti_singleton = GraphitiClient(uri=uri, user=user, password=password)
    return _graphiti_singleton


def get_write_semaphore() -> asyncio.Semaphore:
    """Return an event-loop-local write semaphore using configured concurrency."""
    loop = asyncio.get_running_loop()
    key = id(loop)
    semaphore = _write_semaphores.get(key)
    if semaphore is None:
        limit = max(1, get_config().app.write_semaphore_limit)
        semaphore = asyncio.Semaphore(limit)
        _write_semaphores[key] = semaphore
    return semaphore
