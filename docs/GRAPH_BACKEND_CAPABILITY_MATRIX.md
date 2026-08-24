# 🗄️ Graph backend capability matrix

**Status:** CURRENT MIGRATION CONTRACT / NO MIGRATION AUTHORIZED  
**Active backend:** Neo4j 5.26 LTS (`5.26.29-community` Docker pin)

This matrix exists so a lightweight backend experiment cannot silently become a storage migration.

## Candidates

| Backend | Role | Status |
|---|---|---|
| Neo4j | current durable Graphiti backend | **ACTIVE** |
| LadybugDB | lightweight embedded/serverless property-graph candidate; successor to Kùzu | **ADJACENT / EVALUATE ONLY** |
| KùzuDB | predecessor/legacy lightweight embedded graph project | **HISTORICAL** |

## Mandatory parity gate

A candidate backend may not replace Neo4j until every REQUIRED capability below has an evidence-backed result.

| Capability | Requirement | Neo4j | LadybugDB experiment |
|---|---|---|---|
| Graphiti driver compatibility | REQUIRED | current path | NOT VALIDATED |
| temporal episode semantics | REQUIRED | current path | NOT VALIDATED |
| `group_id` namespace isolation | REQUIRED | current path | NOT VALIDATED |
| exact UUID lookup/update | REQUIRED | current path | NOT VALIDATED |
| constraints / uniqueness | REQUIRED | current path | NOT VALIDATED |
| transactions / failure semantics | REQUIRED | current path | NOT VALIDATED |
| full-text/search primitives needed by Graphiti | REQUIRED | current path | NOT VALIDATED |
| vector/index primitives needed by Graphiti | REQUIRED | current path | NOT VALIDATED |
| community/query compatibility | REQUIRED | current path | NOT VALIDATED |
| backup/export | REQUIRED | supported | NOT VALIDATED |
| restore/import | REQUIRED | supported | NOT VALIDATED |
| migration tooling | REQUIRED | n/a current | NOT DESIGNED |
| rollback to Neo4j | REQUIRED | n/a current | NOT DESIGNED |
| ingest benchmark | REQUIRED | baseline needed | NOT RUN |
| retrieval correctness benchmark | REQUIRED | baseline needed | NOT RUN |
| retrieval latency benchmark | REQUIRED | baseline needed | NOT RUN |
| concurrency behavior | REQUIRED | current path | NOT RUN |
| crash/restart durability | REQUIRED | current path | NOT RUN |

## Migration invariant

```text
one active durable graph authority at a time
```

Forbidden without a separate migration decision:

- dual-write Neo4j + LadybugDB;
- treating two backends as equal memory authorities;
- silent fallback from one backend to another;
- migration without byte/object counts and retrieval-equivalence checks;
- deleting the Neo4j source before rollback evidence exists.

## Evaluation sequence

1. Build an adapter prototype outside the active path.
2. Run synthetic Graphiti compatibility tests.
3. Export a bounded fixture dataset from Neo4j.
4. Import into candidate backend.
5. Compare object counts, UUID identity and namespace boundaries.
6. Compare retrieval result sets and temporal behavior.
7. Run ingest/read/concurrency benchmarks.
8. Test crash/restart, backup and restore.
9. Test rollback to Neo4j.
10. Only then make an explicit owner migration decision.

> Lightweight deployment is a benefit only if semantic compatibility and recovery remain intact.
