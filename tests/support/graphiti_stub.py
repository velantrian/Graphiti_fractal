"""Deterministic recording stand-in for Graphiti on top of a real, admitted Neo4j.

Only ``add_episode`` is simulated (no model, no network): like Graphiti it creates
one ``Episodic`` node and returns the episode result. Everything else the code
under test does (claims, identity finalization, episode lookup) runs as real
Cypher against the disposable database. The stub records every call so tests can
state exactly what reached "Graphiti".
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Awaitable, Callable


class _Result:
    def __init__(self, records):
        self.records = records


class StubDriver:
    def __init__(self, driver, database: str):
        self._driver, self._database = driver, database

    async def execute_query(self, query, **kwargs):
        records, _, _ = await self._driver.execute_query(
            query, parameters_=kwargs, database_=self._database)
        return _Result(records)


class StubGraphiti:
    """``plan`` entries are consumed per add_episode call: a callable
    ``async (stub, kwargs) -> result`` replaces the default successful write."""

    def __init__(self, driver: StubDriver, plan: list[Callable[..., Awaitable]] | None = None):
        self.driver = driver
        self.plan = list(plan or [])
        self.calls: list[dict] = []

    async def add_episode(self, **kwargs):
        self.calls.append({k: kwargs[k] for k in ("name", "source_description", "group_id")})
        if self.plan:
            return await self.plan.pop(0)(self, kwargs)
        return await self.write(kwargs)

    async def write(self, kwargs, *, degraded: bool = False):
        episode_uuid = str(uuid.uuid4())
        await self.driver.execute_query(
            "CREATE (:Episodic {uuid:$uuid, name:$name, content:$content, "
            "source_description:$source_description, group_id:$group_id})",
            uuid=episode_uuid, name=kwargs["name"], content=kwargs["episode_body"],
            source_description=kwargs["source_description"], group_id=kwargs["group_id"])
        # A nameless entity is dropped by core.safe_graphiti -> degraded extraction.
        entities = [SimpleNamespace(uuid="stub-entity", name="")] if degraded else []
        return SimpleNamespace(episode=SimpleNamespace(uuid=episode_uuid),
                               extracted_entities=entities, extracted_edges=[])


def write_then_raise(exc: BaseException):
    """The upstream transaction commits, then the caller sees an error."""
    async def step(stub, kwargs):
        await stub.write(kwargs)
        raise exc
    return step


def raise_without_writing(exc: BaseException):
    async def step(_stub, _kwargs):
        raise exc
    return step


def write_degraded():
    async def step(stub, kwargs):
        return await stub.write(kwargs, degraded=True)
    return step


class RaisingSemaphore:
    """Fails before the write path starts (nothing can have reached Graphiti)."""

    def __init__(self, exc: BaseException):
        self.exc = exc

    async def __aenter__(self):
        raise self.exc

    async def __aexit__(self, *exc_info):
        return False
