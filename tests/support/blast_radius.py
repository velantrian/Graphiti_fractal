"""PROTOTYPE (verification only): read-only affected area of an erased episode.

Computed from Graphiti's own lineage inside ONE group:
  * entities the episode MENTIONS, split into ``pure`` (no other live source mentions
    them) and ``mixed`` (other sources too: their summary text is a blend and must be
    rebuilt, not just hidden);
  * edges whose ``episodes`` contain the episode or that the episode lists in
    ``entity_edges``, split the same way by remaining sources;
  * ``exposed_episodes``: later episodes in the group whose extraction window
    (the 10 previous episodes by valid_at) contained the erased one, an upper bound
    for implicit context influence;
  * ``unknown``: objects of the group with no lineage at all (no MENTIONS / empty
    ``episodes``), which cannot be classified.
Nothing outside the erased episode's group_id is ever matched.
"""

WINDOW = 10

BLAST_RADIUS = """
MATCH (a:Episodic {uuid:$uuid})
WITH a, a.group_id AS g
CALL (a, g) {
    OPTIONAL MATCH (a)-[:MENTIONS]->(n:Entity) WHERE n.group_id = g
    WITH n WHERE n IS NOT NULL
    OPTIONAL MATCH (o:Episodic)-[:MENTIONS]->(n)
        WHERE o.group_id = g AND o.uuid <> a.uuid AND coalesce(o.deleted,false) = false
    WITH n, count(o) AS others
    RETURN collect({uuid:n.uuid, name:n.name, other_sources:others}) AS entities
}
CALL (a, g) {
    OPTIONAL MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity)
        WHERE r.group_id = g AND (a.uuid IN coalesce(r.episodes, []) OR r.uuid IN coalesce(a.entity_edges, []))
    WITH r WHERE r IS NOT NULL
    RETURN collect({uuid:r.uuid, other_sources:size([x IN coalesce(r.episodes, []) WHERE x <> a.uuid])}) AS edges
}
CALL (a, g) {
    OPTIONAL MATCH (e:Episodic {group_id:g})
        WHERE e.uuid <> a.uuid AND e.valid_at >= a.valid_at AND coalesce(e.deleted,false) = false
    WITH e ORDER BY e.valid_at ASC LIMIT $window
    WITH e WHERE e IS NOT NULL
    RETURN collect(e.uuid) AS exposed
}
CALL (g) {
    OPTIONAL MATCH (n:Entity {group_id:g}) WHERE NOT EXISTS { MATCH (:Episodic)-[:MENTIONS]->(n) }
    WITH collect(n.uuid) AS entities_without_lineage
    OPTIONAL MATCH ()-[r:RELATES_TO {group_id:g}]->() WHERE size(coalesce(r.episodes, [])) = 0
    RETURN entities_without_lineage, collect(r.uuid) AS edges_without_lineage
}
RETURN g AS group_id, entities, edges, exposed,
       entities_without_lineage, edges_without_lineage
"""


async def blast_radius(driver, uuid: str) -> dict:
    result = await driver.execute_query(BLAST_RADIUS, uuid=uuid, window=WINDOW)
    row = dict(result.records[0])
    return {
        "group_id": row["group_id"],
        "pure_entities": sorted(e["name"] for e in row["entities"] if e["other_sources"] == 0),
        "mixed_entities": sorted(e["name"] for e in row["entities"] if e["other_sources"] > 0),
        "pure_edges": sorted(e["uuid"] for e in row["edges"] if e["other_sources"] == 0),
        "mixed_edges": sorted(e["uuid"] for e in row["edges"] if e["other_sources"] > 0),
        "exposed_episodes": sorted(row["exposed"]),
        "unknown_lineage": sorted(row["entities_without_lineage"] + row["edges_without_lineage"]),
        "_entity_uuids": sorted(e["uuid"] for e in row["entities"]),
        "_edge_uuids": sorted(e["uuid"] for e in row["edges"]),
    }
