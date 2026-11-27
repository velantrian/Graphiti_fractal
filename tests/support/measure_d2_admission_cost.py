"""One-off measurement (not run in CI): latency of the batched admission before and after the D2 freshness check.

  git show origin/main:core/erasure_guard.py > /tmp/erasure_guard_before.py
  python scripts/test_neo4j.py run -- python tests/support/measure_d2_admission_cost.py /tmp/erasure_guard_before.py

"before" is the module loaded from the given file (identity + erasure state only), "after" is this tree's
core.erasure_guard (also compares text). Synthetic nodes (1 KB texts, half episodes, half entities) in a
disposable Neo4j; interleaved runs after warm-up; numbers describe that environment only.
"""
import asyncio, importlib.util, json, os, statistics, sys, time
sys.path.insert(0, os.getcwd())
from neo4j import AsyncGraphDatabase
from core import erasure_guard as after
from tests.support.neo4j_isolation import IsolatedNeo4jSettings, admit_sync

SIZES = (10, 50, 200, 1000)
RUNS = 25
TEXT = ("The quarterly archive review mentions vault procedures, courier schedules and key custody. " * 12)[:1000]


class G:
    def __init__(self, d, db):
        self.d, self.db, self.driver = d, db, self

    async def execute_query(self, q, **kw):
        r, s, _ = await self.d.execute_query(q, parameters_=kw, database_=self.db)

        class R: pass
        x = R(); x.records = r; return x


def load_before(path):
    spec = importlib.util.spec_from_file_location("erasure_guard_before", path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod


def pct(values, p):
    values = sorted(values); return values[min(len(values) - 1, int(len(values) * p))]


async def main():
    before = load_before(sys.argv[1])
    s = IsolatedNeo4jSettings.from_env(); admit_sync(s)
    d = AsyncGraphDatabase.driver(s.uri, auth=(s.user, s.password)); g = G(d, s.database)
    await d.execute_query("CREATE INDEX m_ep IF NOT EXISTS FOR (n:Episodic) ON (n.uuid)", database_=s.database)
    await d.execute_query("CREATE INDEX m_en IF NOT EXISTS FOR (n:Entity) ON (n.uuid)", database_=s.database)
    top = max(SIZES)
    await d.execute_query("UNWIND range(1,$n) AS i CREATE (:Episodic {uuid:'ep-'+i, group_id:'knowledge', "
                          "content:$t+' '+i, origin_class:'owner'}) CREATE (:Entity {uuid:'en-'+i, group_id:'knowledge', "
                          "name:'Entity '+i, summary:$t+' '+i})", n=top, t=TEXT, database_=s.database)
    await d.execute_query("CALL db.awaitIndexes(120)", database_=s.database)
    out = {}
    for n in SIZES:
        half = n // 2
        plain, rich = [], []
        for i in range(1, half + 1):
            plain += [{"type": "episode", "uuid": f"ep-{i}", "group_id": "knowledge"},
                      {"type": "entity", "uuid": f"en-{i}", "group_id": "knowledge"}]
            rich += [{"type": "episode", "uuid": f"ep-{i}", "group_id": "knowledge", "_raw_text": [f"{TEXT} {i}", ""]},
                     {"type": "entity", "uuid": f"en-{i}", "group_id": "knowledge", "_raw_text": [f"Entity {i}", f"{TEXT} {i}"]}]
        for _ in range(5):
            await before.admit_search_candidates(g, plain); await after.admit_search_candidates(g, rich)
        tb, ta = [], []
        for _ in range(RUNS):
            t0 = time.perf_counter(); b = await before.admit_search_candidates(g, plain); tb.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter(); a = await after.admit_search_candidates(g, rich); ta.append((time.perf_counter() - t0) * 1000)
        assert len(a) == len(b) == len(plain)
        out[str(n)] = {"before_ms_median": round(statistics.median(tb), 1), "before_ms_p95": round(pct(tb, .95), 1),
                       "after_ms_median": round(statistics.median(ta), 1), "after_ms_p95": round(pct(ta, .95), 1),
                       "extra_ms_median": round(statistics.median(ta) - statistics.median(tb), 1),
                       "payload_kb_after": round(len(json.dumps(rich)) / 1024, 1)}
    print("MEASURED_D2 " + json.dumps(out, sort_keys=True))
    await d.close()

asyncio.run(main())
