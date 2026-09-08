import pytest
from queries.context_builder import DEFAULT_CONTEXT_GROUP_IDS, build_agent_context

# Mock classes to simulate Graphiti and Neo4j behavior


class DummySearchResult:
    def __init__(self, edges=None):
        self.edges = edges or []


class DummyEdge:
    def __init__(self, source_node_uuid, target_node_uuid, relationship_type="RELATES_TO"):
        self.source_node_uuid = source_node_uuid
        self.target_node_uuid = target_node_uuid
        self.relationship_type = relationship_type


class DummyRecord:
    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)


class DummyResult:
    def __init__(self, records):
        self.records = records

    async def list(self):
        return self.records


class DummySession:
    def __init__(self, driver):
        self.driver = driver

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def run(self, query, **kwargs):
        return await self.driver.execute_query(query, **kwargs)


class DummyDriver:
    async def execute_query(self, query, **kwargs):
        # Mock bulk fetch response
        if "MATCH (n)" in query and "$uuids" in query:
            uuids = kwargs.get("uuids", [])
            records = []
            for uuid in uuids:
                records.append(
                    DummyRecord(
                        {
                            "uuid": uuid,
                            "labels": ["Entity"],
                            "name": "Velan" if uuid == "src" else "Other",
                            "summary": f"Summary for {uuid}",
                            "content": None,
                            "episode_body": None,
                            "source_description": "test",
                            "deleted": False,
                        }
                    )
                )
            return DummyResult(records)
        return DummyResult([])

    def session(self):
        return DummySession(self)


class DummyGraphiti:
    def __init__(self):
        self.driver = DummyDriver()
        self.last_group_ids = None

    async def search_(self, query, config=None, search_filter=None, group_ids=None):
        self.last_group_ids = group_ids
        if query == "missing":
            return DummySearchResult(edges=[])

        # Return a dummy edge
        edge = DummyEdge("src", "tgt")
        return DummySearchResult(edges=[edge])


@pytest.mark.asyncio
async def test_build_agent_context_returns_none_when_not_found():
    graphiti = DummyGraphiti()
    result = await build_agent_context(graphiti, "missing")
    assert result is None
    assert graphiti.last_group_ids == list(DEFAULT_CONTEXT_GROUP_IDS)


@pytest.mark.asyncio
async def test_build_agent_context_builds_list():
    graphiti = DummyGraphiti()
    result = await build_agent_context(graphiti, "velan", context_size="minimal")

    assert result is not None
    assert "RELATES_TO" in result
    assert "Summary for src" in result
    assert "Summary for tgt" in result
    assert graphiti.last_group_ids == list(DEFAULT_CONTEXT_GROUP_IDS)
