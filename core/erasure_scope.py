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
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from core.erasure_guard import (
    LOCK_ERASURE_CONTROL, OPEN_STATUSES, ErasureFenceChanged, read_erasure_state, token_hashes,
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


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


async def rebuild_area(graphiti, event_id: str | None = None) -> dict:
    """Clear or rebuild what the open erasures marked, with per-object evidence.

    * exposed objects: reopened only if a content screen finds none of the erased text's
      distinctive tokens (tokens also present in other live episodes of the group are ignored);
      a failing exposed object becomes mixed;
    * mixed entities: summary rebuilt deterministically from the facts of clean edges;
    * mixed edges and communities carry erased text that cannot be separated without a model:
      they stay hidden (re-extraction is not implemented), and so does their group's write pause;
    * derived episodes (chat, summaries, L3, any group): never reopened here (owner decision).
    All commits require an unchanged erasure revision; otherwise ErasureFenceChanged is raised
    and nothing already hidden is reopened.
    """
    await scope_pending_erasures(graphiti)
    rev0, _ = await read_erasure_state(graphiti)
    opens = await _rows(graphiti, f"MATCH (t:FractalErasure) WHERE t.status IN {OPEN_STATUSES} "
                                  "RETURN t.event_id AS e, t.group_id AS g, t.token_hashes AS h")
    if event_id:
        opens = [o for o in opens if o["e"] == event_id] or opens
    erased_hashes: set[str] = {h for o in opens for h in (o["h"] or [])}
    live_cache: dict[str, set[str]] = {}

    async def dirty(text: str, group: str) -> bool:
        if group not in live_cache:
            live_cache[group] = await _live_hashes(graphiti, group)
        return bool((set(token_hashes(text, cap=100000)) & erased_hashes) - live_cache[group])

    summary = {"reopened_by_screen": 0, "rebuilt_entities": 0, "still_hidden_edges": 0,
               "still_hidden_communities": 0, "owner_decision_needed": 0}
    for ev in [o["e"] for o in opens]:
        prefix = ev + ":"
        marked = lambda x: f"any(m IN coalesce({x}.erasure_quarantine,[]) WHERE m STARTS WITH $p)"  # noqa: E731
        edges = await _rows(graphiti, f"""
            MATCH (s:Entity)-[r:RELATES_TO]->(t:Entity) WHERE {marked('r')}
            RETURN r.uuid AS uuid, r.group_id AS g, r.fact AS fact, toString(r.created_at) AS ts,
                   [m IN r.erasure_quarantine WHERE m STARTS WITH $p | substring(m, size($p))][0] AS cls,
                   s.uuid AS s, t.uuid AS t, size(r.erasure_quarantine) AS nmarks""", p=prefix)
        ents = await _rows(graphiti, f"""
            MATCH (n:Entity) WHERE {marked('n')}
            RETURN n.uuid AS uuid, n.group_id AS g, n.name AS name, n.summary AS summary,
                   [m IN n.erasure_quarantine WHERE m STARTS WITH $p | substring(m, size($p))][0] AS cls,
                   size(n.erasure_quarantine) AS nmarks""", p=prefix)
        comms = await _rows(graphiti, f"""
            MATCH (n:Community) WHERE {marked('n')}
            RETURN n.uuid AS uuid, n.group_id AS g, n.summary AS summary, size(n.erasure_quarantine) AS nmarks""", p=prefix)
        derived = await _rows(graphiti, f"MATCH (n:Episodic) WHERE {marked('n')} RETURN count(n) AS n", p=prefix)
        summary["owner_decision_needed"] += derived[0]["n"]

        clear_edges, clear_ents, hold_edges = [], [], []
        for r in edges:
            if r["cls"] == "mixed" or await dirty(r["fact"] or "", r["g"]):
                hold_edges.append(r)          # known to carry erased text: needs an extractor
            else:
                clear_edges.append({"uuid": r["uuid"], "evidence": f"{ev}:content_screen:rev{rev0}:{_sha(r['fact'])}"})
        clear_ids = {c["uuid"] for c in clear_edges}
        for r in ents:
            if r["cls"] == "exposed" and not await dirty(r["summary"] or "", r["g"]):
                clear_ents.append({"uuid": r["uuid"], "summary": r["summary"], "evidence":
                                   f"{ev}:content_screen:rev{rev0}:{_sha(r['summary'])}"})
                continue
            clean = [e for e in await _rows(graphiti, f"""
                MATCH (a:Entity {{uuid:$u}})-[r:RELATES_TO]-(:Entity)
                RETURN r.uuid AS uuid, r.fact AS fact, toString(r.created_at) AS ts,
                       size(coalesce(r.erasure_quarantine,[])) AS marks ORDER BY ts""", u=r["uuid"])
                     if e["marks"] == 0 or e["uuid"] in clear_ids]
            facts = list(dict.fromkeys(e["fact"] for e in clean if e["fact"]))
            new_summary = "\n".join(facts) if facts else (r["name"] or "")
            if await dirty(new_summary, r["g"]):
                continue                      # cannot happen from clean facts; stay hidden if it does
            clear_ents.append({"uuid": r["uuid"], "summary": new_summary, "evidence":
                               f"{ev}:rebuilt_from_clean_edges:rev{rev0}:{_sha(new_summary)}:{len(clean)}"})
            summary["rebuilt_entities"] += 1
        summary["still_hidden_edges"] += len(hold_edges)
        summary["still_hidden_communities"] += len(comms)
        summary["reopened_by_screen"] += len(clear_edges) + sum(
            1 for c in clear_ents if ":content_screen:" in c["evidence"])

        await _checkpoint("after_screen")      # a re-erasure here must stop the commit below
        await _commit(graphiti, rev0, prefix, "edge", clear_edges)
        await _commit(graphiti, rev0, prefix, "Entity", clear_ents)

        left = await _rows(graphiti, f"""
            OPTIONAL MATCH (n) WHERE (n:Entity OR n:Community) AND {marked('n')}
            WITH collect(DISTINCT n.group_id) AS g1
            OPTIONAL MATCH ()-[r:RELATES_TO]->() WHERE {marked('r')}
            RETURN g1 + collect(DISTINCT r.group_id) AS groups""", p=prefix)
        groups = sorted({g for g in left[0]["groups"] if g})
        await graphiti.driver.execute_query(
            "MATCH (t:FractalErasure {event_id:$e}) SET t.blocked_groups=$g, "
            "t.status=CASE WHEN size($g)=0 THEN 'RESOLVED' ELSE 'SCOPED' END, "
            "t.resolved_at=CASE WHEN size($g)=0 THEN datetime() ELSE null END, t.owner_decision_needed=$d",
            e=ev, g=groups, d=derived[0]["n"])
    return summary


async def _commit(graphiti, rev: int, prefix: str, label: str, items: list[dict]) -> None:
    if not items:
        return
    ev_expr = "x.erasure_quarantine = [m IN coalesce(x.erasure_quarantine,[]) WHERE NOT m STARTS WITH $prefix], " \
              "x.erasure_evidence = coalesce(x.erasure_evidence,[]) + [it.evidence]"
    if label == "edge":
        match, extra = "MATCH ()-[x:RELATES_TO {uuid:it.uuid}]->()", ""
    else:
        match, extra = "MATCH (x:Entity {uuid:it.uuid})", ", x.summary = it.summary"
    result = await graphiti.driver.execute_query(
        LOCK_ERASURE_CONTROL + " WHERE coalesce(gate.revision,0) = $rev UNWIND $items AS it "
        + match + " SET " + ev_expr + extra + " RETURN count(it) AS n",
        rev=rev, items=items, prefix=prefix)
    if int(result.records[0]["n"]) != len(items):
        raise ErasureFenceChanged("erasure revision changed during rebuild; nothing was reopened")
