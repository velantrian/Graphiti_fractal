import asyncio, os, sys, time, statistics, json
sys.path.insert(0, os.getcwd())
from neo4j import AsyncGraphDatabase
from tests.support.neo4j_isolation import IsolatedNeo4jSettings, admit_sync
from core.erasure_guard import ERASURE_PRESENT, read_erasure_state

class G:  # Graphiti-shaped adapter
    def __init__(self, d): self.d=d; self.driver=self
    async def execute_query(self, q, **kw):
        r,_,_ = await self.d.execute_query(q, parameters_=kw)
        class R: pass
        x=R(); x.records=r; return x

async def main():
    s = IsolatedNeo4jSettings.from_env(); admit_sync(s)
    d = AsyncGraphDatabase.driver(s.uri, auth=(s.user, s.password)); g = G(d)
    out = {}
    for n in (400_000,):
        have = (await d.execute_query("MATCH (n) WHERE NOT n:FractalTestDatabase RETURN count(n) AS c"))[0][0]["c"]
        for start in range(have, n, 50_000):
            await d.execute_query("UNWIND range($a,$b) AS i CREATE (:Episodic {uuid:'bulk-'+toString(i), group_id:'knowledge', content:'x', origin_class:'owner'})", a=start, b=min(start+49_999, n-1))
        # (1) plan + db hits of the quarantine probe with NO deletions (worst case: nothing short-circuits)
        recs = await d.execute_query("PROFILE RETURN " + ERASURE_PRESENT + " AS q")
        plan = recs.summary.profile
        def walk(p, acc):
            acc.append((p["operatorType"], p.get("dbHits",0))); [walk(c, acc) for c in p.get("children",[])]; return acc
        ops = walk(plan, [])
        hits = sum(h for _,h in ops)
        times=[]
        for _ in range(15):
            t=time.perf_counter(); await read_erasure_state(g); times.append((time.perf_counter()-t)*1000)
        out[str(n)] = {"nodes": n, "operators": sorted({o for o,_ in ops}), "db_hits": hits, "read_erasure_state_ms_median": round(statistics.median(times),1)}
    # (2) serialization of application-owned commit decisions through the global control node
    from core.erasure_guard import LOCK_ERASURE_CONTROL
    async def commit(i, hold_scan):
        q = LOCK_ERASURE_CONTROL + (" WHERE coalesce(gate.revision,0)=0 AND " + ERASURE_PRESENT + " = false RETURN 1 AS ok" if hold_scan else " RETURN 1 AS ok")
        await d.execute_query(q)
    res = {}
    for label, scan in (("lock_only (claim acquisition style)", False), ("lock + quarantine scan (chat finalize style)", True)):
        warm=[]
        for _ in range(8):
            t=time.perf_counter(); await commit(0, scan); warm.append((time.perf_counter()-t)*1000)
        single=statistics.median(warm[2:])
        t=time.perf_counter(); await asyncio.gather(*[commit(i, scan) for i in range(16)]); wall=(time.perf_counter()-t)*1000
        res[label] = {"single_warm_median_ms": round(single,1), "16_concurrent_wall_ms": round(wall,1), "wall_over_single": round(wall/single,1)}
    out["serialization_at_400k_nodes"] = res
    print("MEASURED " + json.dumps(out, sort_keys=True))
    await d.close()
asyncio.run(main())
