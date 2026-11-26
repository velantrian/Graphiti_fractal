"""One-off measurement (not run in CI): cost of variant B's state read, reader admission and commit lock.

  python scripts/test_neo4j.py run -- python tests/support/measure_variant_b_cost.py
Synthetic nodes in a disposable Neo4j; numbers describe that environment only.
"""
import asyncio, json, os, statistics, sys, time
sys.path.insert(0, os.getcwd())
from neo4j import AsyncGraphDatabase
from core.erasure_guard import LOCK_ERASURE_CONTROL, admit_search_candidates, read_erasure_state, visible_node
from tests.support.neo4j_isolation import IsolatedNeo4jSettings, admit_sync


class G:
    def __init__(self, d): self.d, self.driver = d, self
    async def execute_query(self, q, **kw):
        r, s, _ = await self.d.execute_query(q, parameters_=kw)
        class R: pass
        x = R(); x.records = r; x.summary = s; return x


def hits(summary):
    def walk(p):
        return p.get("dbHits", 0) + sum(walk(c) for c in p.get("children", []))
    return walk(summary.profile)


async def main():
    s = IsolatedNeo4jSettings.from_env(); admit_sync(s)
    d = AsyncGraphDatabase.driver(s.uri, auth=(s.user, s.password)); g = G(d)
    await d.execute_query("CREATE INDEX ep_uuid IF NOT EXISTS FOR (n:Episodic) ON (n.uuid)")
    out = {}
    for n in (10_000, 100_000, 400_000):
        have = (await d.execute_query("MATCH (n:Episodic) RETURN count(n) AS c"))[0][0]["c"]
        for a in range(have, n, 50_000):
            await d.execute_query("UNWIND range($a,$b) AS i CREATE (:Episodic {uuid:'bulk-'+toString(i), group_id:'knowledge', content:'x', origin_class:'owner'})", a=a, b=min(a + 49_999, n - 1))
        if n == 10_000:   # three tombstones: an erasure history, one still open
            await d.execute_query("UNWIND [1,2,3] AS i CREATE (:FractalErasure {target_uuid:'gone-'+toString(i), group_id:'knowledge', status: CASE WHEN i=3 THEN 'SCOPED' ELSE 'RESOLVED' END, revision:i})")
            await d.execute_query("MERGE (c:FractalErasureControl {key:'global'}) SET c.revision=3")
        await d.execute_query("CALL db.awaitIndexes(120)")
        state = await d.execute_query("PROFILE OPTIONAL MATCH (c:FractalErasureControl {key:'global'}) WITH coalesce(c.revision,0) AS revision "
                                      "RETURN revision, EXISTS { MATCH (t:FractalErasure) WHERE t.status IS NULL OR t.status IN ['PENDING_SCOPE','SCOPED'] } AS open")
        cands = [{"type": "episode", "uuid": f"bulk-{i}", "group_id": "knowledge"} for i in range(50)]
        t = []
        for _ in range(10):
            a0 = time.perf_counter(); await admit_search_candidates(g, cands); t.append((time.perf_counter() - a0) * 1000)
        adm = await d.execute_query("PROFILE UNWIND $c AS candidate CALL (candidate) { MATCH (n:Episodic {uuid:candidate.uuid}) WHERE candidate.kind='episode' AND n.group_id=candidate.group_id AND " + visible_node("n") + " RETURN n.uuid AS uuid } RETURN count(uuid) AS n", c=[{"kind": "episode", "uuid": c["uuid"], "group_id": "knowledge"} for c in cands])
        ops = set()
        def walk(p):
            ops.add(p["operatorType"].split("@")[0]); [walk(c) for c in p.get("children", [])]
        walk(adm.summary.profile)
        times = []
        for _ in range(15):
            a0 = time.perf_counter(); await read_erasure_state(g); times.append((time.perf_counter() - a0) * 1000)
        out[str(n)] = {"read_erasure_state_db_hits": hits(state.summary), "read_erasure_state_ms_median": round(statistics.median(times), 1),
                       "admit_50_candidates_ms_median": round(statistics.median(t), 1), "admit_db_hits": hits(adm.summary),
                       "admit_operators": sorted(ops)}
    async def commit():
        await d.execute_query(LOCK_ERASURE_CONTROL + " WHERE coalesce(gate.revision,0) = 3 RETURN 1 AS ok")
    warm = []
    for _ in range(8):
        a0 = time.perf_counter(); await commit(); warm.append((time.perf_counter() - a0) * 1000)
    single = statistics.median(warm[2:])
    a0 = time.perf_counter(); await asyncio.gather(*[commit() for _ in range(16)]); wall = (time.perf_counter() - a0) * 1000
    out["commit_lock_at_400k_nodes"] = {"single_warm_median_ms": round(single, 1), "16_concurrent_wall_ms": round(wall, 1)}
    print("MEASURED_B " + json.dumps(out, sort_keys=True))
    await d.close()

asyncio.run(main())
