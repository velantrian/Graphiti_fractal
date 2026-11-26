"""Read-only dry run for legacy ``deleted=true`` flags (variant B). Writes nothing.

For every flagged episode it computes, with the same code the real erasure uses
(core.erasure_scope.compute_area), the affected area that migrating the flag to a
tombstone would produce, and states per reason how many objects would be restricted,
purged or held, in which groups, and which groups stay untouched.

Reason codes:
  LEGACY_FLAG_NOT_MIGRATED     flagged object; hidden itself today, its derived objects are not yet
  AREA_PURE / AREA_MIXED / AREA_EXPOSED / AREA_DERIVED   (after migration) purge / rebuild / screen / owner
  GROUP_WRITE_PAUSE            (after migration) group where new model-assisted writes pause until rebuild
  UNKNOWN_LINEAGE_DERIVED_KIND chat/summary/L3 episodes without recorded lineage; restricted once any
                               tombstone exists, until the owner reviews them (lineage_reviewed=true)
"""
from __future__ import annotations

from core.erasure_guard import blocked_node, unknown_lineage
from core.erasure_scope import _rows, compute_area, seed_from_node


async def dry_run(graphiti) -> dict:
    flagged_all = await _rows(graphiti, "MATCH (n) WHERE n.deleted = true "
                                        "RETURN head(labels(n)) AS label, n.group_id AS grp, count(n) AS n")
    flagged_eps = await _rows(graphiti, "MATCH (e:Episodic) WHERE e.deleted = true "
                                        "RETURN e.uuid AS uuid, e.group_id AS grp ORDER BY e.uuid")
    per_episode, area_totals, write_pause, derived_groups = [], {}, set(), set()
    for ep in flagged_eps:
        seed = await seed_from_node(graphiti, ep["uuid"])
        area = await compute_area(graphiti, seed)
        counts = area.counts()
        incident = 0
        pure = [u for u, c in area.entities.items() if c == "pure"]
        if pure:
            incident = (await _rows(graphiti, "MATCH (n:Entity)-[r:RELATES_TO]-() WHERE n.uuid IN $u "
                                              "RETURN count(DISTINCT r) AS c", u=pure))[0]["c"]
        visible = {
            "entities": (await _rows(graphiti, "MATCH (n:Entity) WHERE n.uuid IN $u AND NOT " + blocked_node("n")
                                     + " RETURN count(n) AS c", u=sorted(area.entities)))[0]["c"],
            "edges": (await _rows(graphiti, "MATCH ()-[r:RELATES_TO]->() WHERE r.uuid IN $u RETURN count(r) AS c",
                                  u=sorted(area.edges)))[0]["c"],
            "derived_episodes": (await _rows(graphiti, "MATCH (n:Episodic) WHERE n.uuid IN $u AND NOT "
                                             + blocked_node("n") + " RETURN count(n) AS c", u=sorted(area.derived)))[0]["c"],
        }
        for key, value in counts.items():
            area_totals[key] = area_totals.get(key, 0) + value
        if any(c in ("mixed", "exposed") for c in {**area.entities, **area.edges, **area.communities}.values()):
            write_pause.add(ep["grp"])
        derived_groups.update(area.derived.values())
        per_episode.append({"episode": ep["uuid"], "group": ep["grp"], "area_counts": counts,
                            "edges_removed_with_pure_entities": incident,
                            "derived_objects_visible_today": visible,
                            "derived_groups": sorted(set(area.derived.values()))})

    unknown = await _rows(graphiti, "MATCH (e:Episodic) WHERE " + unknown_lineage("e") + """
        RETURN e.group_id AS grp,
               CASE WHEN coalesce(e.source_description,'') STARTS WITH 'l3_profile:' THEN 'l3_profile'
                    WHEN coalesce(e.episode_kind,'') = 'chat_summary' THEN 'chat_summary' ELSE 'chat_turn' END AS kind,
               count(e) AS n ORDER BY grp, kind""")
    groups = {r["g"] for r in await _rows(graphiti, "MATCH (e:Episodic) WHERE e.group_id IS NOT NULL "
                                                    "RETURN DISTINCT e.group_id AS g")}
    touched = {ep["grp"] for ep in flagged_eps} | derived_groups | write_pause
    visible_total = sum(sum(e["derived_objects_visible_today"].values()) for e in per_episode)
    return {
        "read_only": True,
        "legacy_flagged_objects": flagged_all,
        "flagged_episodes": len(flagged_eps),
        "reasons": {
            "LEGACY_FLAG_NOT_MIGRATED": {
                "objects_hidden_today": sum(r["n"] for r in flagged_all),
                "derived_objects_visible_today_that_migration_would_restrict": visible_total},
            "AREA_AFTER_MIGRATION": area_totals,
            "GROUP_WRITE_PAUSE": sorted(write_pause),
            "UNKNOWN_LINEAGE_DERIVED_KIND": {
                "total": sum(r["n"] for r in unknown), "by_group_and_kind": unknown,
                "effect": "restricted as soon as any tombstone exists, until reviewed"},
        },
        "groups_seen": sorted(groups),
        "groups_touched_after_migration": sorted(touched),
        "groups_untouched_after_migration": sorted(groups - touched),
        "per_episode": per_episode,
        "migration": "not performed; migrating = erase_node(uuid) for each flagged episode (soft), after owner approval",
    }
