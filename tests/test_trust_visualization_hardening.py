from pathlib import Path
from types import SimpleNamespace

import pytest

from layers.l2_semantic import (
    DEFAULT_L2_GROUP_IDS,
    _normalize_allowed_groups,
    get_l2_semantic_context_with_sources,
)
from layers.l3_fractal import L3_SYSTEM_INSTRUCTION
from visualization.visualization_export import export_graph_for_vis


class RecordingDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, **params):
        self.calls.append((query, params))
        return SimpleNamespace(records=[])


class VisualizationDriver:
    async def execute_query(self, query, **params):
        if "RETURN n.uuid as uuid" in query:
            return SimpleNamespace(
                records=[
                    {
                        "uuid": "node-a",
                        "name": "A",
                        "labels": ["Entity"],
                        "group_id": "knowledge",
                        "summary": "first",
                    },
                    {
                        "uuid": "node-b",
                        "name": "B",
                        "labels": ["Community"],
                        "group_id": "knowledge",
                        "summary": "second",
                    },
                ]
            )
        return SimpleNamespace(
            records=[
                {
                    "source": "node-a",
                    "target": "node-b",
                    "type": "RELATES_TO",
                    "fact": "bounded edge",
                }
            ]
        )


def test_l2_default_groups_exclude_imports():
    assert "imports" not in DEFAULT_L2_GROUP_IDS
    assert set(DEFAULT_L2_GROUP_IDS) == {"personal", "project", "knowledge", "experience"}


def test_l2_refuses_imports_even_when_requested_explicitly():
    with pytest.raises(ValueError, match="quarantined imports"):
        _normalize_allowed_groups(["knowledge", "imports"])


@pytest.mark.asyncio
async def test_l2_query_is_bound_to_trusted_groups():
    driver = RecordingDriver()
    graphiti = SimpleNamespace(driver=driver)

    context, source_ids = await get_l2_semantic_context_with_sources(graphiti, "example")

    assert context is None
    assert source_ids == []
    assert len(driver.calls) == 1
    query, params = driver.calls[0]
    assert "e.group_id IN $allowed_groups" in query
    assert "coalesce(c.group_id, e.group_id) IN $allowed_groups" in query
    assert "origin_class" in query
    assert "imports" not in params["allowed_groups"]


@pytest.mark.asyncio
async def test_visualization_export_uses_d3_edge_contract():
    graphiti = SimpleNamespace(driver=VisualizationDriver())

    data = await export_graph_for_vis(graphiti, limit=10)

    assert data["statistics"]["total_nodes"] == 2
    assert data["statistics"]["total_edges"] == 1
    edge = data["edges"][0]
    assert edge["source"] == "node-a"
    assert edge["target"] == "node-b"
    assert "from" not in edge
    assert "to" not in edge


def test_visualization_does_not_render_graph_data_with_inner_html():
    html = Path("static/visualization.html").read_text(encoding="utf-8")

    assert "tooltip.innerHTML" not in html
    assert "insertAdjacentHTML" not in html
    assert "strong.textContent = label" in html
    assert "document.createTextNode(title || '')" in html


def test_l3_system_instruction_treats_memory_as_data_not_authority():
    normalized = L3_SYSTEM_INSTRUCTION.lower()

    assert "untrusted data" in normalized
    assert "never as instructions" in normalized
    assert "do not upgrade" in normalized
