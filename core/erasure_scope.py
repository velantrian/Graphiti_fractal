"""Affected area of an erasure, its containment, rebuild and evidence (variant B).

Lineage used (all explicit in the graph, no model involved):
  * Graphiti: Episodic-[:MENTIONS]->Entity, RELATES_TO.episodes, Episodic.entity_edges,
    Community-[:HAS_MEMBER]->Entity, and the previous-episode window (10 episodes) plus every
    later episode that mentions an already affected entity ("exposed": its extraction saw
    affected text, so its derived objects are suspect);
  * Fractal: derived_source_ids, recall_source_ids, summarized_turns on chat turns, chat
    summaries and L3 profiles, followed transitively to any depth and across groups.
Classes: pure (only the erased text produced it; purged), mixed (blended with other sources;
rebuilt), exposed (suspect; cleared by a content screen or rebuilt), derived (agent-derived
episodes in the chain; kept hidden until the owner decides).

Phase 2 (scope_erasure) is idempotent; rebuild_area commits only at an unchanged erasure
revision and records per-object evidence. Nothing here deletes a tombstone.

Evidence that reopens an object is *re-derivation from clean live sources*: an edge fact is
extracted again from its live source episode with no previous episodes, an entity summary is
rebuilt from such edges, and an object no live source supports is deleted. A token screen of the
stored text is only a post-check on the re-derived text, never the basis: a paraphrase of erased
content carries none of its tokens.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from core.erasure_guard import (
    LOCK_ERASURE_CONTROL, OPEN_STATUSES, ErasureFenceChanged, blocked_node, read_erasure_state, token_hashes,
)

WINDOW = 10
_LIVE = "coalesce({a}.deleted,false) = false AND NOT EXISTS {{ MATCH (:FractalErasure {{target_uuid:{a}.uuid}}) }}"


def _live(alias: str) -> str:
    return _LIVE.format(a=alias)


async def _checkpoint(name: str) -> None:  # test hook: lets a test interleave an event
    return None


async def _rows(graphiti, cypher: str, **params) -> list[dict]:
    return [dict(r) for r in (await graphiti.driver.execute_query(cypher, **params)).records]


@dataclass
class Seed:
    target_uuid: str
    group_id: str
    valid_at: str
    entities: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)


@dataclass
class Area:
    entities: dict[str, str] = field(default_factory=dict)     # uuid -> pure|mixed|exposed
    edges: dict[str, str] = field(default_factory=dict)
    communities: dict[str, str] = field(default_factory=dict)
    derived: dict[str, str] = field(default_factory=dict)      # episode uuid -> group_id
    exposed_episodes: list[str] = field(default_factory=list)

    def counts(self) -> dict:
        out: dict[str, int] = {}
        for label, items in (("entity", self.entities), ("edge", self.edges), ("community", self.communities)):
            for cls in items.values():
                out[f"{label}_{cls}"] = out.get(f"{label}_{cls}", 0) + 1
        out["derived_episode"] = len(self.derived)
        out["exposed_episode"] = len(self.exposed_episodes)
        return out


async def seed_from_node(graphiti, episode_uuid: str) -> Seed | None:
    """Seed for an episode that still exists (dry run, or legacy-flagged episodes)."""
    rows = await _rows(graphiti, """
        MATCH (n:Episodic {uuid:$u})
        OPTIONAL MATCH (n)-[:MENTIONS]->(m:Entity)
        WITH n, collect(DISTINCT m.uuid) AS ents
        OPTIONAL MATCH ()-[r:RELATES_TO]->()
          WHERE r.group_id = n.group_id AND (n.uuid IN coalesce(r.episodes,[]) OR r.uuid IN coalesce(n.entity_edges,[]))
        RETURN n.group_id AS g, toString(n.valid_at) AS va, ents, collect(DISTINCT r.uuid) AS edges
        """, u=episode_uuid)
    if not rows:
        return None
    r = rows[0]
    return Seed(episode_uuid, r["g"], r["va"] or "", list(r["ents"]), list(r["edges"]))


async def compute_area(graphiti, seed: Seed) -> Area:
    t, g = seed.target_uuid, seed.group_id
    area = Area()
    if seed.entities:
        for r in await _rows(graphiti, f"""
            UNWIND $ids AS id MATCH (e:Entity {{uuid:id}})
            OPTIONAL MATCH (o:Episodic)-[:MENTIONS]->(e) WHERE o.uuid <> $t AND {_live('o')}
            RETURN e.uuid AS uuid, count(o) AS others""", ids=seed.entities, t=t):
            area.entities[r["uuid"]] = "mixed" if r["others"] else "pure"
    if seed.edges:
        for r in await _rows(graphiti, """
            UNWIND $ids AS id MATCH ()-[r:RELATES_TO {uuid:id}]->()
            RETURN r.uuid AS uuid, size([x IN coalesce(r.episodes,[]) WHERE x <> $t]) AS others""",
                             ids=seed.edges, t=t):
            area.edges[r["uuid"]] = "mixed" if r["others"] else "pure"

    # Exposure: the previous-episode window, plus every later episode mentioning an affected entity.
    later = await _rows(graphiti, f"""
        MATCH (e:Episodic {{group_id:$g}}) WHERE e.uuid <> $t AND {_live('e')} AND toString(e.valid_at) >= $va
        WITH e ORDER BY e.valid_at ASC LIMIT $w RETURN e.uuid AS uuid""", g=g, t=t, va=seed.valid_at, w=WINDOW)
    exposed = {r["uuid"] for r in later}
    area.exposed_episodes = sorted(exposed)
    while True:
        rows = await _rows(graphiti, f"""
            MATCH (e:Episodic {{group_id:$g}})
            WHERE e.uuid <> $t AND {_live('e')} AND toString(e.valid_at) >= $va
              AND (e.uuid IN $exposed OR EXISTS {{ MATCH (e)-[:MENTIONS]->(n:Entity) WHERE n.uuid IN $affected }})
            RETURN e.uuid AS uuid, [(e)-[:MENTIONS]->(n:Entity) | n.uuid] AS mentioned""",
                           g=g, t=t, va=seed.valid_at, exposed=sorted(exposed), affected=sorted(area.entities))
        grew = False
        for r in rows:
            if r["uuid"] not in exposed:
                exposed.add(r["uuid"])
                grew = True
            for u in r["mentioned"]:
                if u not in area.entities:
                    area.entities[u] = "exposed"
                    grew = True
        if not grew:
            break
    area.exposed_episodes = sorted(exposed)
    if exposed:
        for r in await _rows(graphiti, """
            MATCH ()-[r:RELATES_TO {group_id:$g}]->()
            WHERE any(x IN coalesce(r.episodes,[]) WHERE x IN $exposed) RETURN DISTINCT r.uuid AS uuid""",
                             g=g, exposed=sorted(exposed)):
            area.edges.setdefault(r["uuid"], "exposed")

    if area.entities:
        for r in await _rows(graphiti, """
            MATCH (c:Community {group_id:$g})-[:HAS_MEMBER]->(e:Entity) WHERE e.uuid IN $ids
            RETURN c.uuid AS uuid, collect(e.uuid) AS members""", g=g, ids=sorted(area.entities)):
            hard = any(area.entities[m] in ("pure", "mixed") for m in r["members"])
            area.communities[r["uuid"]] = "mixed" if hard else "exposed"

    await extend_lineage(graphiti, area, seed,
                         {t} | {u for u, c in {**area.entities, **area.edges, **area.communities}.items()
                                if c in ("pure", "mixed")})
    return area


async def extend_lineage(graphiti, area: Area, seed: Seed, frontier: set[str]) -> None:
    """Follow explicit derived/recall/summary lineage lists to any depth, in any group."""
    known = set(area.derived) | {seed.target_uuid}
    frontier = set(frontier)
    while frontier:
        rows = await _rows(graphiti, """
            MATCH (x:Episodic)
            WHERE x.uuid <> $t AND NOT x.uuid IN $known
              AND any(id IN coalesce(x.derived_source_ids,[]) + coalesce(x.recall_source_ids,[])
                         + coalesce(x.summarized_turns,[]) WHERE id IN $frontier)
            RETURN x.uuid AS uuid, x.group_id AS g""", t=seed.target_uuid, known=sorted(known), frontier=sorted(frontier))
        frontier = set()
        for r in rows:
            area.derived[r["uuid"]] = r["g"]
            known.add(r["uuid"])
            frontier.add(r["uuid"])


async def _mark(graphiti, event_id: str, label: str, items: dict[str, str]) -> None:
    if not items:
        return
    payload = [{"uuid": u, "cls": c} for u, c in sorted(items.items())]
    strip = "[m IN coalesce(@V.erasure_quarantine,[]) WHERE NOT m STARTS WITH $prefix] + [$prefix + it.cls]"
    if label == "edge":
        cypher = ("UNWIND $items AS it MATCH ()-[x:RELATES_TO {uuid:it.uuid}]->() SET x.erasure_quarantine = "
                  + strip.replace("@V", "x"))
    else:
        cypher = (f"UNWIND $items AS it MATCH (x:{label} {{uuid:it.uuid}}) SET x.erasure_quarantine = "
                  + strip.replace("@V", "x"))
    await graphiti.driver.execute_query(cypher, items=payload, prefix=event_id + ":")


async def _load(graphiti, event_id: str) -> dict:
    rows = await _rows(graphiti, "MATCH (t:FractalErasure {event_id:$e}) RETURN properties(t) AS t", e=event_id)
    if not rows:
        raise LookupError(f"unknown erasure event {event_id}")
    return rows[0]["t"]


def _seed(t: dict) -> Seed:
    return Seed(t["target_uuid"], t.get("group_id") or "", t.get("valid_at") or "",
                list(t.get("seed_entities") or []), list(t.get("seed_edges") or []))


async def scope_erasure(graphiti, event_id: str) -> dict:
    """Phase 2: compute the area, purge pure derived objects, mark the rest, publish counts."""
    tomb = await _load(graphiti, event_id)
    if tomb.get("status") != "PENDING_SCOPE":
        return {"status": tomb.get("status"), "event_id": event_id, "counts": tomb.get("area_counts_json")}
    seed = _seed(tomb)
    area = await compute_area(graphiti, seed) if tomb.get("target_label") == "Episodic" else Area()

    pure_edges = [u for u, c in area.edges.items() if c == "pure"]
    pure_entities = [u for u, c in area.entities.items() if c == "pure"]
    if pure_edges:
        await graphiti.driver.execute_query(
            "MATCH ()-[r:RELATES_TO]->() WHERE r.uuid IN $u DELETE r", u=pure_edges)
    if pure_entities:
        await graphiti.driver.execute_query("MATCH (n:Entity) WHERE n.uuid IN $u DETACH DELETE n", u=pure_entities)
    await _mark(graphiti, event_id, "Entity", {u: c for u, c in area.entities.items() if c != "pure"})
    await _mark(graphiti, event_id, "edge", {u: c for u, c in area.edges.items() if c != "pure"})
    await _mark(graphiti, event_id, "Community", area.communities)
    await _mark(graphiti, event_id, "Episodic", {u: "derived" for u in area.derived})

    blocked = sorted({seed.group_id} if (area.entities or area.edges or area.communities) else set())
    counts = area.counts()
    counts["purged_entities"], counts["purged_edges"] = len(pure_entities), len(pure_edges)
    await graphiti.driver.execute_query(
        "MATCH (t:FractalErasure {event_id:$e}) SET t.status='SCOPED', t.scoped_at=datetime(), "
        "t.blocked_groups=$blocked, t.area_counts_json=$counts",
        e=event_id, blocked=blocked, counts=_json(counts))
    return {"status": "SCOPED", "event_id": event_id, "counts": counts, "blocked_groups": blocked,
            "derived_groups": sorted(set(area.derived.values()))}


def _json(d: dict) -> str:
    import json
    return json.dumps(d, sort_keys=True)


async def scope_pending_erasures(graphiti) -> list[dict]:
    """Crash recovery: finish phase 2 for every tombstone still PENDING_SCOPE."""
    rows = await _rows(graphiti, "MATCH (t:FractalErasure {status:'PENDING_SCOPE'}) RETURN t.event_id AS e")
    return [await scope_erasure(graphiti, r["e"]) for r in rows]


# --- rebuild --------------------------------------------------------------------------------
async def _live_hashes(graphiti, group_id: str) -> set[str]:
    out: set[str] = set()
    for r in await _rows(graphiti, f"""
        MATCH (e:Episodic {{group_id:$g}}) WHERE {_live('e')}
        RETURN coalesce(e.content, e.episode_body, '') AS c""", g=group_id):
        out.update(token_hashes(r["c"], cap=100000))
    return out


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def graphiti_edge_extractor(graphiti):
    """Production extractor: Graphiti's own extract_edges on ONE live episode.

    The input is exactly the episode text plus the names of the entities it mentions, with no
    previous episodes, so it cannot contain text of an erased episode. Returns
    ``[{"source": uuid, "target": uuid, "name": relation type, "fact": str, "input_sha256": str}]``.
    Direction (source -> target) and relation name are part of what a fact states and are matched on.
    """
    from graphiti_core.nodes import EntityNode, EpisodicNode
    from graphiti_core.utils.maintenance.edge_operations import extract_edges

    async def extract(episode_uuid: str) -> list[dict]:
        episode = await EpisodicNode.get_by_uuid(graphiti.driver, episode_uuid)
        mentioned = [r["u"] for r in await _rows(graphiti, "MATCH (:Episodic {uuid:$e})-[:MENTIONS]->(n:Entity) "
                                                           "RETURN n.uuid AS u", e=episode_uuid)]
        nodes = await EntityNode.get_by_uuids(graphiti.driver, mentioned)
        edges = await extract_edges(graphiti.clients, episode, nodes, [], {("Entity", "Entity"): []}, episode.group_id)
        digest = _sha(episode.content + "|" + "|".join(sorted(n.name for n in nodes)))
        return [{"source": e.source_node_uuid, "target": e.target_node_uuid, "name": e.name, "fact": e.fact,
                 "input_sha256": digest}
                for e in edges]

    return extract


async def rebuild_area(graphiti, event_id: str | None = None, *, extractor=None) -> dict:
    """Re-derive or delete what open erasures marked; reopen objects only with such evidence.

    * edges (exposed/mixed): all live source episodes are found and ALL are consulted before the edge's
      fate is decided. No live source -> nothing can support it: deleted. Otherwise the fact must be
      stated by a source with the same direction and relation name; the first source that states it
      decides. The match must be unambiguous (own text equal to one clean fact, or one edge and one
      fact); an ambiguous match, a fact only in the reverse direction, or a fact the extraction
      skipped leaves the edge restricted for reconciliation (it is NOT deleted: a skipped fact is
      indistinguishable from an unsupported one). A re-derived fact that still fails the token screen
      stays hidden. Without an extractor edges stay hidden.
    * entities (exposed/mixed): no live source mentions them -> deleted; otherwise the summary is
      rebuilt from the facts of its incident edges once every incident edge is clean or re-derived.
    * communities carry erased text that cannot be separated without a model: they stay hidden.
    * derived episodes (chat, summaries, L3, any group): never reopened here (owner decision).
    The group's write pause lasts while any Graphiti-derived object remains marked. All commits
    require an unchanged erasure revision (ErasureFenceChanged otherwise; nothing is reopened).
    """
    summary = {"re_extracted_edges": 0, "purged_unsupported_edges": 0, "purged_unsupported_entities": 0,
               "rebuilt_entities": 0, "held_edges": 0, "held_unmatched_edges": 0, "held_ambiguous_edges": 0,
               "held_entities": 0, "held_communities": 0, "held_for_missing_extractor": 0,
               "owner_decision_needed": 0}
    all_open = await _rows(graphiti, f"MATCH (t:FractalErasure) WHERE t.status IN {OPEN_STATUSES} "
                                     "RETURN t.event_id AS e, t.group_id AS g, t.token_hashes AS h, t.status AS s")
    if event_id is not None:
        # One named event and nothing else: an unknown or closed id returns before any write, and a
        # valid id never scopes, clears or rewrites another event.
        target = [o for o in all_open if o["e"] == event_id]
        if not target:
            return {**summary, "status": "unknown_or_closed_event", "event_id": event_id}
        if target[0]["s"] == "PENDING_SCOPE":
            await scope_erasure(graphiti, event_id)
        opens = target
    else:
        await scope_pending_erasures(graphiti)
        opens = await _rows(graphiti, f"MATCH (t:FractalErasure) WHERE t.status IN {OPEN_STATUSES} "
                                      "RETURN t.event_id AS e, t.group_id AS g, t.token_hashes AS h")
    rev0, _ = await read_erasure_state(graphiti)
    # screen hashes of every open event are read (never written) so the post-check is as strict as possible
    erased_hashes: set[str] = {h for o in all_open for h in (o["h"] or [])}
    live_cache: dict[str, set[str]] = {}

    async def dirty(text: str, group: str) -> bool:     # post-check only, never the basis
        if group not in live_cache:
            live_cache[group] = await _live_hashes(graphiti, group)
        return bool((set(token_hashes(text, cap=100000)) & erased_hashes) - live_cache[group])

    async def live_sources(episode_ids: list[str]) -> list[str]:
        if not episode_ids:
            return []
        return [r["u"] for r in await _rows(graphiti, "MATCH (e:Episodic) WHERE e.uuid IN $u AND NOT "
                                            + blocked_node("e") + " RETURN e.uuid AS u", u=episode_ids)]

    for ev in [o["e"] for o in opens]:
        prefix = ev + ":"
        marked = lambda x: f"any(m IN coalesce({x}.erasure_quarantine,[]) WHERE m STARTS WITH $p)"  # noqa: E731
        edges = await _rows(graphiti, f"""
            MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity) WHERE {marked('r')}
            RETURN r.uuid AS uuid, r.group_id AS g, r.fact AS fact, r.name AS name,
                   coalesce(r.episodes,[]) AS episodes, s.uuid AS s, t.uuid AS t""", p=prefix)
        ents = await _rows(graphiti, f"""
            MATCH (n:Entity) WHERE {marked('n')}
            RETURN n.uuid AS uuid, n.group_id AS g, n.name AS name""", p=prefix)
        comms = await _rows(graphiti, f"MATCH (n:Community) WHERE {marked('n')} RETURN count(n) AS n", p=prefix)
        derived = await _rows(graphiti, f"MATCH (n:Episodic) WHERE {marked('n')} RETURN count(n) AS n", p=prefix)
        summary["owner_decision_needed"] += derived[0]["n"]
        summary["held_communities"] += comms[0]["n"]

        extracted_cache: dict[str, list[dict]] = {}
        purge_edges, cleared_edges, held_edge_ids = [], [], set()
        pending: list[tuple[dict, list[str]]] = []
        for r in edges:
            live_set = set(await live_sources(r["episodes"]))
            live = [u for u in r["episodes"] if u in live_set]       # keep the edge's own source order
            if not live:
                purge_edges.append(r["uuid"])            # no live source exists that could support it
                continue
            if extractor is None:
                held_edge_ids.add(r["uuid"]); summary["held_for_missing_extractor"] += 1
                continue
            pending.append((r, live))
        # Consult EVERY live source of every edge before any decision about any edge.
        for _, live in pending:
            for ep in live:
                if ep not in extracted_cache:
                    extracted_cache[ep] = await extractor(ep)

        def candidates(ep: str, r: dict) -> list[dict]:
            """Facts a source states for exactly this edge: same direction and same relation name."""
            return [c for c in extracted_cache[ep]
                    if c["source"] == r["s"] and c["target"] == r["t"]
                    and (r["name"] is None or c.get("name") in (None, r["name"]))]

        groups: dict[tuple, list[dict]] = {}
        for r, live in pending:
            primary = next((ep for ep in live if candidates(ep, r)), None)
            if primary is None:                          # no source states it (or extraction skipped it)
                held_edge_ids.add(r["uuid"]); summary["held_unmatched_edges"] += 1
                continue
            groups.setdefault((primary, r["s"], r["t"], r["name"], len(live)), []).append(r)
        for (primary, s_uuid, t_uuid, rname, n_live), members in groups.items():
            facts = candidates(primary, members[0])
            # a clean sibling edge of the same relation already carries one of these facts
            clean = {_norm(x["fact"]) for x in await _rows(graphiti, """
                MATCH (:Entity {uuid:$s})-[r:RELATES_TO]->(:Entity {uuid:$t})
                WHERE $ep IN coalesce(r.episodes,[]) AND size(coalesce(r.erasure_quarantine,[])) = 0
                  AND ($n IS NULL OR r.name = $n) RETURN r.fact AS fact""", s=s_uuid, t=t_uuid, ep=primary, n=rname)}
            free = [c for c in {_norm(c["fact"]): c for c in facts}.values() if _norm(c["fact"]) not in clean]
            remaining, pairs = list(members), []
            for r in list(remaining):                    # the edge's own text already equals a clean fact
                hit = next((c for c in free if _norm(c["fact"]) == _norm(r["fact"])), None)
                if hit is not None:
                    pairs.append((r, hit)); remaining.remove(r); free.remove(hit)
            if len(remaining) == 1 and len(free) == 1:   # one edge, one fact: unambiguous
                pairs.append((remaining[0], free[0])); remaining = []
            for r in remaining:
                held_edge_ids.add(r["uuid"])
                # no fact left for it: nothing states it (or extraction skipped it); otherwise the choice is unclear
                summary["held_unmatched_edges" if not free else "held_ambiguous_edges"] += 1
            for r, item in pairs:
                if await dirty(item["fact"], r["g"]):
                    held_edge_ids.add(r["uuid"])         # even the clean derivation carries erased tokens
                    continue
                cleared_edges.append({"uuid": r["uuid"], "fact": item["fact"], "evidence":
                                      f"{ev}:re_extracted:rev{rev0}:in{item['input_sha256']}:out{_sha(item['fact'])}:src{n_live}"})
        summary["held_edges"] += len(held_edge_ids)
        fact_of = {c["uuid"]: c["fact"] for c in cleared_edges}
        gone_edges = set(purge_edges)

        purge_entities, clear_ents = [], []
        for r in ents:
            if not (await _rows(graphiti, "MATCH (n:Entity {uuid:$u}) OPTIONAL MATCH (e:Episodic)-[:MENTIONS]->(n) "
                                          "WHERE NOT " + blocked_node("e") + " RETURN count(e) AS c", u=r["uuid"]))[0]["c"]:
                purge_entities.append(r["uuid"])
                continue
            incident = await _rows(graphiti, """
                MATCH (a:Entity {uuid:$u})-[r:RELATES_TO]-(:Entity)
                RETURN r.uuid AS uuid, r.fact AS fact, size(coalesce(r.erasure_quarantine,[])) AS marks,
                       toString(r.created_at) AS ts ORDER BY ts""", u=r["uuid"])
            incident = [e for e in incident if e["uuid"] not in gone_edges]
            if any(e["marks"] > 0 and e["uuid"] not in fact_of for e in incident):
                summary["held_entities"] += 1            # an incident edge is not clean yet
                continue
            facts = list(dict.fromkeys(fact_of.get(e["uuid"], e["fact"]) for e in incident
                                       if fact_of.get(e["uuid"], e["fact"])))
            text = "\n".join(facts) if facts else (r["name"] or "")
            if await dirty(text, r["g"]):
                summary["held_entities"] += 1
                continue
            clear_ents.append({"uuid": r["uuid"], "summary": text, "evidence":
                               f"{ev}:rebuilt_from_clean_edges:rev{rev0}:{_sha(text)}:{len(incident)}"})
        summary["re_extracted_edges"] += len(cleared_edges)
        summary["purged_unsupported_edges"] += len(purge_edges)
        summary["purged_unsupported_entities"] += len(purge_entities)
        summary["rebuilt_entities"] += len(clear_ents)

        await _checkpoint("after_screen")      # a re-erasure here must stop every commit below
        await _purge(graphiti, rev0, "edge", purge_edges)
        await _purge(graphiti, rev0, "Entity", purge_entities)
        await _commit(graphiti, rev0, prefix, "edge", cleared_edges)
        await _commit(graphiti, rev0, prefix, "Entity", clear_ents)

        left = await _rows(graphiti, f"""
            OPTIONAL MATCH (n:Entity|Community) WHERE {marked('n')}
            WITH collect(DISTINCT n.group_id) AS g1
            OPTIONAL MATCH ()-[r:RELATES_TO]->() WHERE {marked('r')}
            WITH g1, collect(DISTINCT r.group_id) AS g2
            RETURN g1 + g2 AS groups""", p=prefix)
        groups = sorted({g for g in left[0]["groups"] if g})
        await graphiti.driver.execute_query(
            "MATCH (t:FractalErasure {event_id:$e}) SET t.blocked_groups=$g, "
            "t.status=CASE WHEN size($g)=0 THEN 'RESOLVED' ELSE 'SCOPED' END, "
            "t.resolved_at=CASE WHEN size($g)=0 THEN datetime() ELSE null END, t.owner_decision_needed=$d",
            e=ev, g=groups, d=derived[0]["n"])
    return summary


async def _purge(graphiti, rev: int, label: str, ids: list[str]) -> None:
    """Delete derived objects no live source supports; fenced like every rebuild commit."""
    if not ids:
        return
    match = ("OPTIONAL MATCH ()-[x:RELATES_TO {uuid:id}]->()" if label == "edge"
             else "OPTIONAL MATCH (x:Entity {uuid:id})")
    delete = "DELETE x" if label == "edge" else "DETACH DELETE x"
    result = await graphiti.driver.execute_query(
        LOCK_ERASURE_CONTROL + " WHERE coalesce(gate.revision,0) = $rev UNWIND $ids AS id "
        + match + " WITH id, x " + delete + " RETURN count(id) AS n", rev=rev, ids=ids)
    if int(result.records[0]["n"]) != len(ids):
        raise ErasureFenceChanged("erasure revision changed during rebuild; nothing was reopened")


async def _commit(graphiti, rev: int, prefix: str, label: str, items: list[dict]) -> None:
    if not items:
        return
    ev_expr = "x.erasure_quarantine = [m IN coalesce(x.erasure_quarantine,[]) WHERE NOT m STARTS WITH $prefix], " \
              "x.erasure_evidence = coalesce(x.erasure_evidence,[]) + [it.evidence]"
    if label == "edge":
        match, extra = "MATCH ()-[x:RELATES_TO {uuid:it.uuid}]->()", ", x.fact = it.fact"
    else:
        match, extra = "MATCH (x:Entity {uuid:it.uuid})", ", x.summary = it.summary"
    result = await graphiti.driver.execute_query(
        LOCK_ERASURE_CONTROL + " WHERE coalesce(gate.revision,0) = $rev UNWIND $items AS it "
        + match + " SET " + ev_expr + extra + " RETURN count(it) AS n",
        rev=rev, items=items, prefix=prefix)
    if int(result.records[0]["n"]) != len(items):
        raise ErasureFenceChanged("erasure revision changed during rebuild; nothing was reopened")


async def mark_late_write(graphiti, group_id: str, since_revision: int, episode_uuids: list[str]) -> dict:
    """Contain objects of a write that was already in flight when an erasure happened in its group.

    add_episode reads its context, calls the model and writes its derived objects in one long
    call; an erasure that lands meanwhile cannot have scoped objects that did not exist yet.
    Every entity/edge derived from the given episodes is marked as exposed to each erasure of the
    group newer than ``since_revision`` (its tombstone is reopened, so the group is paused), and
    the episodes themselves are isolated until reconciliation.
    """
    if not episode_uuids:
        return {"marked": 0}
    tombs = await _rows(graphiti, "MATCH (t:FractalErasure {group_id:$g}) WHERE t.revision > $r "
                                  "RETURN t.event_id AS e, t.status AS s", g=group_id, r=since_revision)
    if not tombs:
        return {"marked": 0}
    ents = [r["u"] for r in await _rows(graphiti, "MATCH (e:Episodic)-[:MENTIONS]->(n:Entity) WHERE e.uuid IN $u "
                                                  "RETURN DISTINCT n.uuid AS u", u=episode_uuids)]
    edges = [r["u"] for r in await _rows(graphiti, "MATCH ()-[r:RELATES_TO {group_id:$g}]->() "
                                                   "WHERE any(x IN coalesce(r.episodes,[]) WHERE x IN $u) "
                                                   "RETURN DISTINCT r.uuid AS u", g=group_id, u=episode_uuids)]
    await graphiti.driver.execute_query(
        "MATCH (e:Episodic) WHERE e.uuid IN $u SET e.erasure_quarantined = true", u=episode_uuids)
    for t in tombs:
        await _mark(graphiti, t["e"], "Entity", {u: "exposed" for u in ents})
        await _mark(graphiti, t["e"], "edge", {u: "exposed" for u in edges})
        await graphiti.driver.execute_query(
            "MATCH (t:FractalErasure {event_id:$e}) SET t.blocked_groups=[$g], "
            "t.status=CASE WHEN t.status = 'PENDING_SCOPE' THEN 'PENDING_SCOPE' ELSE 'SCOPED' END", e=t["e"], g=group_id)
    return {"marked": len(ents) + len(edges), "events": [t["e"] for t in tombs]}
