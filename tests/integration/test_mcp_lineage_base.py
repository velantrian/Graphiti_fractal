"""Reproduction of the MCP lineage gap on the unchanged contract (a client that does not send a read receipt).

These tests pin what the legacy path (``memory.remember`` / ``memory.upload`` with ``origin_class=agent_derived``
and nothing else) does after the source is deleted. They stay green after the fix on purpose: they are the
documented limit for old clients and old records. The receipt path is tested in test_mcp_lineage.py.
"""
import pytest

from tests.integration.mcp_lineage_support import (  # noqa: F401  (env is a fixture)
    CONTROL, D_TEXT, P, RET, SRC, env, erase, graph_rows, mcp, observe, read_source, seed_source,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("hard", [False, True], ids=["soft", "hard"])
async def test_legacy_retelling_without_a_receipt_survives_deletion_of_its_source(env, hard):
    src = await seed_source(env)
    await read_source(env, src)
    env.llm.expect(D_TEXT)
    stored = await mcp("memory.remember", text=D_TEXT, memory_type="project", source_description="client_retelling")
    assert stored["completeness"] == "complete"
    before = await observe(env)
    print("OBSERVED_BEFORE", {k: sorted(v) for k, v in before.items() if isinstance(v, set)})
    assert SRC in before["search_all"] and RET in before["episodes"] and RET in before["entities"] and RET in before["edges"]
    assert {SRC, RET, CONTROL} <= before["model_input"], "recall really ran: source, retelling and control reach the model"

    await erase(env, src, hard)
    after = await observe(env)
    print("OBSERVED_AFTER", {k: sorted(v) for k, v in after.items() if isinstance(v, set)})
    assert SRC not in after["search_all"] and SRC not in after["mcp_search"] and SRC not in after["model_input"]
    # the gap: nothing links the retelling to its source, so it stays everywhere
    assert RET in after["episodes"] and RET in after["entities"] and RET in after["edges"]
    assert RET in after["mcp_search"] and RET in after["model_input"]
    assert CONTROL in after["model_input"]


@pytest.mark.asyncio
async def test_lineage_set_by_hand_now_reaches_the_graph_objects_too(env):
    """Before the area closure, lineage set by hand reached the dependent EPISODE only: Entity.summary and edge.fact
    extracted from it in the other group stayed visible (the reproduced gap). Now the same lineage hides them as well."""
    src = await seed_source(env)
    env.llm.expect(D_TEXT)
    await mcp("memory.remember", text=D_TEXT, memory_type="project", source_description="client_retelling")
    before = await observe(env, ask=False)
    assert RET in before["episodes"] and RET in before["entities"] and RET in before["edges"]
    await env.q("MATCH (e:Episodic {group_id:$g}) WHERE e.content CONTAINS $t SET e.recall_source_ids=[$s], e.recall_derived=true",
                g=P, t=RET, s=src)
    await erase(env, src, hard=False)
    after = await observe(env, ask=False)
    assert RET not in after["search_all"] and RET not in after["mcp_search"]
    assert not [r for r in await graph_rows(env, RET) if r["label"] in ("Entity", "edge")]
