"""Run the user-facing read paths of ONE code version over the scenario-A data.

  python scenario_a_probe.py <code_root> <verify_root>

``code_root`` is the tree whose code is exercised (main or PR #54); it is put first
on sys.path. ``verify_root`` supplies only the test support modules, loaded by file
path so the two trees' ``tests`` packages cannot collide. Prints one JSON line.
Read-only except for what the code under test itself writes (recall telemetry).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sys
from pathlib import Path

code_root, verify_root = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
sys.path.insert(0, str(code_root))


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, verify_root / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


guard = _load("sa_guard", "tests/support/neo4j_isolation.py")
models = _load("sa_models", "tests/support/recording_model.py")
data = _load("sa_data", "tests/support/scenario_a_dataset.py")
MARK = re.compile(r"MK[A-Za-z0-9]+")


def markers(obj) -> list[str]:
    return sorted(set(MARK.findall(json.dumps(obj, default=str, ensure_ascii=False))))


async def main() -> None:
    from graphiti_core import Graphiti

    settings = guard.IsolatedNeo4jSettings.from_env()
    guard.admit_sync(settings)
    graphiti = Graphiti(settings.uri, settings.user, settings.password, llm_client=models.RecordingLLM(),
                        embedder=models.HashEmbedder(), cross_encoder=models.ZeroRanker())
    out: dict = {"code_root": code_root.name, "groups": {}}
    try:
        from core.memory_ops import MemoryOps
        from knowledge.retrieval import search_knowledge
        from layers.l2_semantic import get_l2_semantic_context_with_sources
        from layers.l3_fractal import get_l3_fractal_context
        from visualization.visualization_export import export_graph_for_vis

        for letter, group in data.GROUPS.items():
            query = " ".join([f"MK{k}{letter}" for k in ("doc", "leg", "chat", "sum", "ent", "edg", "com")]
                             + [f"Atlas{letter}", f"Cluster{letter}", "MKxK", "MKderxK", "MKedgxK", "HiddenK"])
            memory = await MemoryOps(graphiti, "owner").search_memory(query, scopes=[group], limit=50)
            parts = {"episodes": memory.episodes, "entities": memory.entities,
                     "edges": memory.edges, "communities": memory.communities}
            knowledge = await search_knowledge(graphiti, query, group_id=group, limit=50)
            context, sources = await get_l2_semantic_context_with_sources(
                graphiti, f"Atlas{letter}", allowed_group_ids=[group])
            l3 = await get_l3_fractal_context(graphiti, f"Atlas{letter}")
            out["groups"][letter] = {
                "recall": {k: markers(v) for k, v in parts.items() if markers(v)},
                "knowledge_search": markers(knowledge),
                "l2": markers([context, sources]),
                "l3": markers(l3),
            }
        graph = await export_graph_for_vis(graphiti, limit=500)
        by_group: dict = {}
        for node in graph.get("nodes", []):
            by_group.setdefault(str(node.get("group_id")), set()).update(markers(node))
        out["visualization"] = {g: sorted(v) for g, v in by_group.items()}
        out["visualization_node_count"] = len(graph.get("nodes", []))
    finally:
        await graphiti.close()
    print("PROBE_JSON " + json.dumps(out, sort_keys=True))


asyncio.run(main())
