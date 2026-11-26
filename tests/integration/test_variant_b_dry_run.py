"""Dry run for legacy deleted=true flags: read-only, honest about reason and scale, and exact.

The same synthetic data as scenario A (variant "old_flag": one flagged episode, no tombstone;
two groups; legacy chat turns and an L3 profile without recorded lineage).
"""
import hashlib
import json

import pytest
from neo4j.exceptions import ClientError

from core.erasure_dry_run import dry_run
from core.erasure_guard import erase_node
from tests.support import scenario_a_dataset as data
from scripts.erasure_dry_run import ReadOnlyGraph, render


async def fingerprint(graph):
    nodes = await graph.driver.execute_query("MATCH (n) RETURN labels(n) AS l, properties(n) AS p")
    rels = await graph.driver.execute_query("MATCH ()-[r]->() RETURN type(r) AS t, properties(r) AS p")
    rows = sorted(json.dumps([r["l"], r["p"]], sort_keys=True, default=str) for r in nodes.records) + \
        sorted(json.dumps([r["t"], r["p"]], sort_keys=True, default=str) for r in rels.records)
    blob = "\n".join(rows)
    return len(nodes.records), len(rels.records), hashlib.sha256(blob.encode()).hexdigest()


@pytest.mark.asyncio
async def test_dry_run_reports_reason_and_scale_without_writing_and_matches_the_real_migration(isolated_graph, neo4j_isolation):
    await isolated_graph.reset()
    await data.load(isolated_graph.driver.execute_query, "old_flag")
    read_only = ReadOnlyGraph(isolated_graph._raw, neo4j_isolation.database)
    before = await fingerprint(isolated_graph)

    report = await dry_run(read_only)
    assert await fingerprint(isolated_graph) == before                     # nothing was written
    with pytest.raises(ClientError, match="(?i)read access mode"):          # and the adapter cannot write
        await read_only.driver.execute_query("CREATE (:ShouldNotExist)")
    text = render(report)
    for code in ("LEGACY_FLAG_NOT_MIGRATED", "AREA_AFTER_MIGRATION", "GROUP_WRITE_PAUSE", "UNKNOWN_LINEAGE_DERIVED_KIND"):
        assert code in text
    print("DRY_RUN " + json.dumps({k: v for k, v in report.items() if k != "per_episode"}, sort_keys=True, default=str))
    print("DRY_RUN_EPISODE " + json.dumps(report["per_episode"], sort_keys=True))

    assert report["flagged_episodes"] == 1 and report["read_only"] is True
    assert report["reasons"]["LEGACY_FLAG_NOT_MIGRATED"]["objects_hidden_today"] == 1
    unknown = report["reasons"]["UNKNOWN_LINEAGE_DERIVED_KIND"]
    assert unknown["total"] == 3 and {(r["grp"], r["kind"], r["n"]) for r in unknown["by_group_and_kind"]} == {
        ("knowledge", "chat_turn", 1), ("knowledge", "l3_profile", 1), ("project", "chat_turn", 1)}
    assert report["groups_untouched_after_migration"] == ["project"]       # independence shown by lineage
    episode = report["per_episode"][0]
    assert episode["derived_objects_visible_today"]["entities"] >= 1       # residue main also shows today

    # Fidelity: the real migration produces exactly the predicted area.
    result = await erase_node(isolated_graph.sibling(), uuid=episode["episode"])
    real = {k: v for k, v in result["area"]["counts"].items() if not k.startswith("purged_")}
    assert real == episode["area_counts"]
    assert result["area"]["counts"]["purged_entities"] == episode["area_counts"]["entity_pure"]


@pytest.mark.asyncio
async def test_diagnostics_list_objects_without_lineage_and_seedless_tombstones_without_content(isolated_graph, neo4j_isolation):
    await isolated_graph.reset()
    await data.load(isolated_graph.driver.execute_query, "both")           # flag AND a tombstone created without a seed
    await isolated_graph.driver.execute_query(
        "CREATE (:Entity {uuid:'orphan-entity', group_id:'knowledge', name:'OrphanK', summary:'lineage cannot reach me'})")
    read_only = ReadOnlyGraph(isolated_graph._raw, neo4j_isolation.database)
    before = await fingerprint(isolated_graph)
    report = await dry_run(read_only, details=True)
    assert await fingerprint(isolated_graph) == before                      # still read-only
    reasons, detail = report["reasons"], report["details"]
    print("DIAG " + json.dumps({"reasons": reasons, "effects": report["effects_by_moment"]}, sort_keys=True, default=str))

    # tombstone created outside this code: no area can be computed, its group is held
    assert reasons["TOMBSTONE_WITHOUT_SEED"]["total"] == 1
    assert detail["TOMBSTONE_WITHOUT_SEED"]["objects"] == [{"uuid": "x-K", "group": "knowledge"}]
    # objects lineage cannot reach: reported, and honestly labelled as not restricted
    gap = reasons["LINEAGE_GAP"]
    assert {r["grp"]: r["n"] for r in gap["entities_without_mentions"]}["knowledge"] >= 1
    assert "not restricted" in gap["effect"] and "UNKNOWN" in gap["effect"] and gap["content"] == "unknown"
    assert "UNKNOWN" in render(report)                                       # the text report says so too
    assert {"uuid": "orphan-entity", "group": "knowledge", "label": "Entity"} in detail["LINEAGE_GAP"]["objects"]
    # derived-kind episodes without lineage, per object
    unknown = {(o["uuid"], o["kind"]) for o in detail["UNKNOWN_LINEAGE_DERIVED_KIND"]["objects"]}
    assert unknown == {("chat-K", "chat_turn"), ("chat-P", "chat_turn"), ("l3-P", "l3_profile")}
    assert detail["UNKNOWN_LINEAGE_DERIVED_KIND"]["truncated"] is False
    # the report carries identifiers and counts, never stored text
    blob = json.dumps(report, default=str)
    for secret in ("MKxK", "MKdocK", "MKlegK", "lineage cannot reach me", "secret episode"):
        assert secret not in blob
    assert set(report["effects_by_moment"]) == {"on_deploy", "on_first_erasure_with_this_code", "on_migration_of_a_flag"}
    assert "knowledge" in report["groups_touched_after_migration"] and "project" in report["groups_untouched_after_migration"]
