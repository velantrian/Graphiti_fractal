# 🔗 Derived Graph Plane + Lightweight Graph Backends

**Status:** CURRENT RESEARCH/DERIVED CONTRACT  
**Snapshot:** 2026-08-24  
**Runtime authority remains:** Graphiti + Neo4j on the current Fractal path.

This document adds causal/provenance/evidence/analytics concepts **without creating a second memory authority** and records lightweight graph-database alternatives for future backend evaluation.

> **Derived analysis ≠ stored fact. Causal hypothesis ≠ correlation. Predicted link ≠ relationship. Community ≠ truth. Alternative backend ≠ active runtime.**

---

## 1. 🧠 Derived analysis plane

Current authoritative memory path remains:

```text
Episode / Entity / Relation
          ↓
       Graphiti
          ↓
        Neo4j
```

New derived plane:

```text
Graphiti/Neo4j memory
        ↓ read only
 ┌──────┼──────────┬─────────────┐
 ↓      ↓          ↓             ↓
Causal  Provenance Evidence     GDS
hypoth. lineage    topology   analytics
 └──────┴──────────┴──────┬──────┘
                          ↓
                    derived result
                          ↓
                 NEVER auto-promoted
```

Implementation contract: `core/derived_graph.py`.

---

## 2. 🔗 Causal Graph — hypothesis layer

A causal relation is not inferred merely because two entities are connected or events co-occur.

Fractal models causality as an explicit **CausalHypothesis** with:

- `cause_id`;
- `effect_id`;
- causal method/assumption source;
- status (`HYPOTHESIS`, `SUPPORTED`, `CONTRADICTED`, `INCONCLUSIVE`);
- confidence profile;
- optional lag;
- candidate confounders;
- supporting episode IDs;
- contradicting episode IDs.

Example conceptual shape:

```text
Event A
   │
   └──> CausalHypothesis H17 ───> Event B
                 │
                 ├── SUPPORTED_BY → Episode 1
                 ├── SUPPORTED_BY → Episode 2
                 ├── CONTRADICTED_BY → Episode 3
                 └── confounders: [...] 
```

The current implementation only **explains/normalizes** a hypothesis and always returns:

```text
authoritative_fact = false
writes_performed   = false
```

### Future research adapters

Potential opt-in research tools include PyWhy/DoWhy and causal-learn methods such as PC/FCI/GES/LiNGAM. They are **not runtime dependencies** in Fractal today.

Primary conceptual boundary from causal-inference tooling: causal conclusions depend on explicit assumptions about the data-generating process and confounding; graph adjacency alone is insufficient.

---

## 3. 🧾 Provenance / lineage graph

Fractal now has a side-effect-free provenance contract inspired by W3C PROV concepts:

- **Entity** — derived claim/result;
- **Activity** — bounded synthesis/extraction/transformation;
- **Agent** — Fractal/model/user/tool;
- `wasDerivedFrom` — source episode IDs;
- `wasGeneratedBy` — transformation/activity;
- `wasAssociatedWith` — responsible agent.

Example:

```text
SourceDocument
      ↓
   Episode
      ↓
 DerivedClaim
      ↓
CausalHypothesis
```

A derived object must be able to answer **where it came from** before it is useful for explanation or later review.

---

## 4. ⚖️ Support / contradiction topology

Derived claims may link to evidence using explicit relation classes:

- `SUPPORTS`;
- `CONTRADICTS`;
- `REFINES`;
- `SUPERSEDES`.

This topology is intentionally separate from ordinary Graphiti relation semantics.

Useful pattern:

```text
Claim A
 ├── SUPPORTED_BY → Episode X
 ├── CONTRADICTED_BY → Episode Y
 ├── REFINES → Claim B
 └── SUPERSEDES → Claim C
```

No relation here automatically rewrites or deletes source memory.

---

## 5. 📊 Confidence / uncertainty profile

Fractal derived analysis separates dimensions instead of accepting one model-generated confidence number:

- source quality;
- evidence count;
- source independence;
- recency;
- causal strength;
- contradiction penalty.

The local score is an **analysis/ranking aid**, not a probability-of-truth guarantee.

> **LLM confidence ≠ evidence confidence.**

---

## 6. 🧮 Neo4j GDS — read-side analytics only

Neo4j Graph Data Science supports graph algorithms including community detection, centrality, similarity, pathfinding and link prediction.

For Fractal the safe policy is:

- allow `stream`;
- allow `stats`;
- block `mutate`;
- block `write`.

`core/derived_graph.py::plan_gds_analysis()` enforces this fail-closed boundary.

Candidate research algorithms:

- Louvain / Leiden → community-aware retrieval experiments;
- centrality → derived ranking signal;
- shortest/pathfinding → relationship explanations;
- link prediction → **candidate link only**, never factual relation.

No GDS result is authoritative and no algorithm is installed/activated by this document alone.

---

# 7. 🗄️ Graph database choices

## Neo4j — current active backend

**Status: ACTIVE.**

Fractal currently uses Neo4j 5.26 LTS under Graphiti. It remains the validated baseline and is pinned in Docker to `neo4j:5.26.29-community`.

Reasons to keep it now:

- current Graphiti integration already works against this backend family;
- mature Cypher/property-graph tooling;
- existing project migrations, queries and tests target it;
- changing the database is a migration/compatibility project, not a dependency refresh.

---

## KùzuDB — historical lightweight embedded alternative

**Status: HISTORICAL / LEGACY ALTERNATIVE.**

Kùzu was an embedded property-graph database designed around local/analytical graph workloads and a Cypher-like query model. It was attractive for applications that wanted a graph database embedded directly in-process instead of operating a separate Neo4j server.

However, the original Kùzu project was archived in October 2025. It should therefore not be presented as the preferred fresh backend for a new Fractal migration in 2026.

Why keep it in the technology history:

- it represents the embedded/serverless graph-database design point;
- old ecosystem material may still reference Kùzu;
- its lineage explains the current LadybugDB project.

**Fractal decision:** document only; do not add as a runtime dependency.

---

## LadybugDB — current lightweight successor option

**Status: ADJACENT / BACKEND CANDIDATE.**

LadybugDB is the active successor/continuation of Kùzu (the project explicitly identifies itself as formerly Kuzu). It keeps the lightweight embedded/serverless property-graph direction and is a more appropriate modern candidate when the goal is to reduce operational weight versus a standalone Neo4j service.

Potential Fractal advantages to evaluate:

- embedded/in-process deployment;
- fewer moving services for a local single-user installation;
- property-graph query model suitable for local graph workloads;
- potentially simpler packaging for desktop/offline deployments.

But a backend switch is **not** currently authorized because Fractal depends on Graphiti's concrete backend support and on existing Neo4j-oriented queries/migrations.

Required gate before any LadybugDB adoption:

1. verify current Graphiti driver/backend support or design a bounded adapter;
2. run semantic parity tests for episode/entity/edge/community behavior;
3. verify temporal fields and indexes;
4. verify vector/full-text/search behavior needed by Graphiti;
5. port migrations and direct Cypher carefully;
6. benchmark local ingest/search and memory use;
7. verify backup/recovery/data portability;
8. run the same fail-closed auth/namespace/dedupe contracts;
9. keep Neo4j as rollback/reference until parity is demonstrated.

Until those gates pass:

```text
Neo4j      = ACTIVE
LadybugDB  = ADJACENT / OPTIONAL FUTURE BACKEND
KùzuDB     = HISTORICAL / LEGACY ALTERNATIVE
```

---

## 8. Backend selection principle

Fractal should select a graph backend by deployment requirement, not fashion.

| Requirement | Likely direction |
|---|---|
| mature current Graphiti path | Neo4j |
| local embedded/serverless future path | evaluate LadybugDB |
| historical embedded references | KùzuDB history |
| relational product metadata | PostgreSQL, separate concern |
| vector-in-Postgres | pgvector, separate concern |

The target architecture should expose **one active durable graph authority at a time**.

Do not run Neo4j + LadybugDB as parallel competing memory stores merely to have options.

---

## 9. What is implemented now vs research

### Implemented now

- causal-hypothesis data contract;
- provenance-lineage contract;
- support/contradiction topology contract;
- confidence-profile contract;
- read-side GDS policy (`stream|stats` only);
- always-on tests proving no derived writes;
- documentation of Neo4j/Kùzu/LadybugDB roles.

### Not implemented / not claimed

- no automatic causal discovery;
- no DoWhy/cause-learn runtime dependency;
- no automatic causal write-back;
- no GDS runtime dependency or algorithm execution;
- no predicted-link write-back;
- no LadybugDB/Kùzu driver in runtime;
- no backend migration away from Neo4j.

---

## 10. Primary sources checked

- W3C PROV primer: https://www.w3.org/TR/prov-primer/
- PyWhy / DoWhy documentation: https://www.pywhy.org/dowhy/
- causal-learn documentation: https://causal-learn.readthedocs.io/
- Neo4j GDS documentation: https://neo4j.com/docs/graph-data-science/current/
- archived Kùzu repository: https://github.com/kuzudb/kuzu
- LadybugDB project: https://github.com/LadybugDB/ladybug

These sources establish concepts/project status. Adoption decisions above are Fractal-specific and remain bounded by the current runtime contracts.
