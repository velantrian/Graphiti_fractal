"""Synthetic dataset for scenario A (upgrade of an existing memory), shared by the
loader (orchestrator) and the probe (runs under main's or PR #54's code).

Two groups: ``knowledge`` (an episode is erased there) and ``project`` (nothing is
ever deleted). Per group: an owner source document, a legacy episode and a legacy
chat turn WITHOUT ``origin_class``, a chat summary, an entity with summary, a related
entity, an edge fact, a community, and L3 profiles. Marker tokens encode kind and
group: ``MK<kind><K|P>``; ``MK*x*K`` markers belong to the erased episode and the
objects derived from it.
"""
from __future__ import annotations

GROUPS = {"K": "knowledge", "P": "project"}
VARIANTS = ("none", "old_flag", "tombstone", "both")

DERIVED_FROM_ERASED = ("MKderxK", "MKedgxK")
ERASED = "MKxK"
TS = "2026-01-01T00:00:00Z"
VEC = [0.1] * 16


def _ep(uuid, group, content, **extra):
    props = {"uuid": uuid, "group_id": group, "content": content, "name": uuid, "entity_edges": [],
             "source": "text", "valid_at": "2026-01-01T00:00:00Z", "created_at": "2026-01-01T00:00:00Z"}
    props.update(extra)
    return props


async def load(execute, variant: str) -> None:
    """``execute(cypher, **params)`` is an async callable; identical for every code version."""
    assert variant in VARIANTS
    await execute("MERGE (:User {user_id:'owner'})")
    for letter, group in GROUPS.items():
        ent, beta, com = f"Atlas{letter}", f"Beta{letter}", f"Cluster{letter}"
        await execute(
            """
            MERGE (u:User {user_id:'owner'})
            CREATE (doc:Episodic $doc), (leg:Episodic $leg), (chat:Episodic $chat), (sum:Episodic $sum)
            CREATE (e:Entity {uuid:$eu, group_id:$g, name:$ent, summary:$es, created_at:$ts, name_embedding:$vec})
            CREATE (b:Entity {uuid:$bu, group_id:$g, name:$beta, summary:'plain beta', created_at:$ts, name_embedding:$vec})
            CREATE (c:Community {uuid:$cu, group_id:$g, name:$com, summary:$cs, level:0, created_at:$ts, name_embedding:$vec})
            CREATE (e)-[:RELATES_TO {uuid:$ru, group_id:$g, fact:$fact, name:'RELATES_TO', episodes:[], created_at:$ts, fact_embedding:$vec}]->(b)
            CREATE (c)-[:HAS_MEMBER]->(e), (doc)-[:MENTIONS]->(e)
            CREATE (u)-[:AUTHORED]->(leg), (u)-[:AUTHORED]->(chat)
            """,
            ts=TS, vec=VEC, g=group, ent=ent, beta=beta, com=com, eu=f"ent-{letter}", bu=f"beta-{letter}", cu=f"com-{letter}",
            ru=f"edge-{letter}", es=f"{ent} MKent{letter} summary", cs=f"MKcom{letter} cluster summary",
            fact=f"{ent} MKedg{letter} relates to {beta}",
            doc=_ep(f"doc-{letter}", group, f"MKdoc{letter} owner source document", origin_class="owner",
                    source_description="uploaded_text", fingerprint=f"fp-doc-{letter}"),
            leg=_ep(f"leg-{letter}", group, f"MKleg{letter} older note", source_description="user_chat"),
            chat=_ep(f"chat-{letter}", group, f"User: hi\nAssistant: MKchat{letter} reply", source_description="chat",
                     episode_kind="chat_turn", conversation_id="c1", turn_index=1),
            sum=_ep(f"sum-{letter}", group, f"MKsum{letter} chat summary", origin_class="agent_derived",
                    source_description="chat", episode_kind="chat_summary", derived_source_ids=[f"chat-{letter}"],
                    summarized_turns=[f"chat-{letter}"]),
        )
    # L3 profiles (not group-scoped by design): one with lineage, one legacy without.
    await execute(
        "CREATE (:Episodic $a), (:Episodic $b)",
        a=_ep("l3-K", "knowledge", "MKl3K profile of AtlasK", origin_class="agent_derived",
              source_description="l3_profile:AtlasK", derived_source_ids=["doc-K"]),
        b=_ep("l3-P", "knowledge", "MKl3P legacy profile of AtlasP", origin_class="agent_derived",
              source_description="l3_profile:AtlasP"),
    )
    # The episode that is erased in variants, plus objects derived from it.
    live = variant in ("none",)
    await execute(
        """
        CREATE (d:Entity {uuid:'ent-x', group_id:'knowledge', name:'HiddenK', summary:'HiddenK MKderxK derived from erased',
                          created_at:$ts, name_embedding:$vec})
        CREATE (f:Entity {uuid:'ent-x2', group_id:'knowledge', name:'HiddenPartnerK', summary:'plain',
                          created_at:$ts, name_embedding:$vec})
        CREATE (d)-[:RELATES_TO {uuid:'edge-x', group_id:'knowledge', fact:'HiddenK MKedgxK relates to HiddenPartnerK',
                                 name:'RELATES_TO', episodes:[], created_at:$ts, fact_embedding:$vec}]->(f)
        """, ts=TS, vec=VEC)
    if variant != "tombstone":
        props = _ep("x-K", "knowledge", "MKxK secret episode to be erased", origin_class="owner",
                    source_description="uploaded_text", fingerprint="fp-x")
        if variant in ("old_flag", "both"):
            props.update(deleted=True, deleted_at="2026-02-01T00:00:00+00:00")
        await execute("CREATE (e:Episodic $p) WITH e MATCH (n:Entity {uuid:'ent-x'}) CREATE (e)-[:MENTIONS]->(n)", p=props)
    if variant in ("tombstone", "both"):
        await execute(
            "CREATE (:FractalErasure {target_uuid:'x-K', group_id:'knowledge', mode:'hard', "
            "event_id:'ev-1', created_at:'2026-02-01T00:00:00+00:00'}) "
            "CREATE (:FractalErasureControl {key:'global', revision:1, fence_serial:1})")
    _ = live
