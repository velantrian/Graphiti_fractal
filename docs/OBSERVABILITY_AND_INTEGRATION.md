# 📡 Recall telemetry, provenance IDs, and live Neo4j validation

**Status:** CURRENT BOUNDED CONTRACT  
**Snapshot:** 2026-08-24

## Recall telemetry

Fractal now records actual retrieval usage from `MemoryOps.build_context_for_query()` after the normal search result is already available.

Telemetry is deliberately separate from memory semantics:

```text
SearchResult UUIDs
      ↓
RecallTelemetry --SEEN_IN_QUERY--> RecallQuery
```

Rules:

- query text is not stored; only normalized SHA-256 query fingerprint is persisted;
- telemetry has no `fact`, `valid_at`, or `invalid_at` semantics;
- telemetry does not change search score, truth, validity, or provenance;
- telemetry failure is best-effort and cannot fail the answer path;
- `recall_count` and `unique_queries` are operational signals for future promotion evaluation, not proof of truth.

## Provenance IDs

`core/provenance.py` creates deterministic identities for derived artifacts from:

- artifact kind;
- source IDs;
- activity;
- optional payload digest.

The same sources in a different order produce the same provenance ID. Derived provenance records remain `authoritative_fact=false`.

## Graph analytics preview CLI

```bash
python scripts/graph_analytics_preview.py louvain
python scripts/graph_analytics_preview.py pagerank --mode stats
python scripts/graph_analytics_preview.py shortest-path
```

This command only emits a plan. It does not install or execute Neo4j GDS. Fractal policy continues to allow only `stream|stats` and rejects `mutate|write`.

## Backend parity gate

See `GRAPH_BACKEND_CAPABILITY_MATRIX.md` for Neo4j/LadybugDB/Kùzu migration requirements. LadybugDB is a future candidate only; Neo4j remains active.

## Provider-free live Neo4j CI

`.github/workflows/neo4j-integration.yml` starts the exact reviewed Neo4j Docker image and runs database contracts without OpenAI/API keys.

Current purpose:

- validate connectivity against real Neo4j;
- create/read uniqueness constraints;
- verify atomic operational telemetry updates;
- verify query-diversity counting;
- prove operational telemetry does not become a Graphiti fact/temporal record.

This complements, but does not replace, the separate opt-in Graphiti + OpenAI ingestion integration tier.
