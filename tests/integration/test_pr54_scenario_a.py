"""Scenario A (PR #54 verification): upgrading an existing memory.

The same synthetic data is loaded by identical Cypher and then read by main's code
(92dfabd) and by PR #54's code (698239b), each in its own process. Variants:
no deletion, only the legacy ``deleted=true`` flag, only a tombstone, both.
Results are synthetic-data results only.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from graphiti_core import Graphiti

from tests.support import scenario_a_dataset as data
from tests.support.recording_model import HashEmbedder, RecordingLLM, ZeroRanker

ROOT = Path(__file__).resolve().parents[2]
CODE = {"main": os.environ.get("FRACTAL_SCENARIO_MAIN_ROOT"), "pr54": os.environ.get("FRACTAL_SCENARIO_PR54_ROOT"),
        "variant_b": str(ROOT)}   # this tree

pytestmark = pytest.mark.skipif(not all(CODE.values()),
                                reason="set FRACTAL_SCENARIO_MAIN_ROOT and FRACTAL_SCENARIO_PR54_ROOT")


@pytest_asyncio.fixture
async def indexes(isolated_graph, neo4j_isolation):
    s = neo4j_isolation
    graphiti = Graphiti(s.uri, s.user, s.password, llm_client=RecordingLLM(),
                        embedder=HashEmbedder(), cross_encoder=ZeroRanker())
    await graphiti.build_indices_and_constraints()
    yield isolated_graph
    await graphiti.close()
    await isolated_graph.reset()


def _probe(code_root):
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEO4J_")}
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tests/support/scenario_a_probe.py"), code_root, str(ROOT)],
        cwd=code_root, env=env, capture_output=True, text=True, timeout=300)
    lines = [l for l in proc.stdout.splitlines() if l.startswith("PROBE_JSON ")]
    assert lines, proc.stderr[-2000:]
    return json.loads(lines[-1][len("PROBE_JSON "):])


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", data.VARIANTS)
async def test_scenario_a_same_data_main_vs_pr54(indexes, variant):
    results = {}
    for name, code_root in CODE.items():
        await indexes.reset()
        await data.load(indexes.driver.execute_query, variant)
        await indexes.driver.execute_query("CALL db.awaitIndexes(120)")
        results[name] = _probe(code_root)
    print("SCENARIO_A " + json.dumps({"variant": variant, **results}, sort_keys=True))
    # Harness validity: with nothing deleted, main must see the data (otherwise the probe is blind).
    main, pr54, vb = results["main"], results["pr54"], results["variant_b"]
    for letter in data.GROUPS:   # harness validity: main sees the data in every variant
        assert main["groups"][letter]["recall"] and main["groups"][letter]["knowledge_search"]
    if variant == "none":
        # No deletion: identical, except a legacy L3 profile without lineage disappears.
        for letter in data.GROUPS:
            m, p = dict(main["groups"][letter]), dict(pr54["groups"][letter])
            if letter == "P":
                assert m.pop("l3") == ["MKl3P"] and p.pop("l3") == []
            assert m == p
        assert main["visualization"] == pr54["visualization"]
    else:
        # Any prior erasure (old flag, tombstone or both) switches the global quarantine on.
        for letter in data.GROUPS:
            seen = pr54["groups"][letter]
            assert seen["recall"] == {"episodes": [f"MKdoc{letter}"]}
            assert seen["knowledge_search"] == [f"MKdoc{letter}"]
            assert seen["l2"] == [] and seen["l3"] == []
        assert pr54["visualization"] == {} and pr54["visualization_node_count"] == 0
        # main keeps showing the whole group-P data, and the objects derived from the erased text.
        assert set(main["groups"]["P"]["recall"]) == {"episodes", "entities", "edges"}
        assert "MKderxK" in main["groups"]["K"]["knowledge_search"]
        if variant != "tombstone":
            assert "MKxK" in main["groups"]["K"]["recall"]["episodes"]   # soft-deleted episode still recalled on main
