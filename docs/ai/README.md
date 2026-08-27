# AI / Agent Entrypoint

Document role: machine-first repository router.

This file is intentionally concise and non-narrative. Human readers should start with [`../../README.md`](../../README.md) and [`../../SYSTEM_OVERVIEW.md`](../../SYSTEM_OVERVIEW.md).

## Project identity

```yaml
project: Graphiti Fractal / Fractal Memory
repository: velantrian/Graphiti_fractal
architecture_role: local-first single-owner AI memory service on top of Graphiti + Neo4j
primary_memory_engine: graphiti_core
primary_graph_backend: neo4j
runtime_authorization_from_docs: false
```

## Required reading order

1. `docs/ai/README.md` — this routing contract.
2. `README.md` — stable project scope and operator entrypoints; do not treat prose as live runtime evidence.
3. `SYSTEM_OVERVIEW.md` — human architecture explanation and explicit non-claims.
4. `core/model_policy.py` — active model defaults and override policy.
5. `knowledge/ingest.py` — canonical text-ingest path.
6. `core/memory_ops.py` and `core/memory_lifecycle.py` — retrieval/lifecycle boundaries.
7. `docs/TECHNOLOGY_EVOLUTION.md` — technology roles and ACTIVE/ADJACENT/RESEARCH distinctions.
8. `docs/AI_MODEL_EVOLUTION.md` — model/provider history and current policy context.
9. `docs/OPENCLAW_ADOPTED_PATTERNS.md` — bounded pattern adoption and rejected boundaries.
10. Relevant tests, workflow files, live PR exact head and GitHub Actions evidence for the task being evaluated.

## Authority order

For current behavior, prefer evidence in this order:

```text
live exact repository state / exact PR head
  > executable tests and workflow evidence
  > active code and configuration
  > current architecture docs
  > historical/research docs
```

A historical document, old SHA, old CI run, roadmap item, or research note must not override newer live code/state.

## Core invariants

```yaml
invariants:
  - retrieval_is_not_evidence
  - graph_is_not_truth
  - imported_is_not_trusted
  - frequency_is_not_authority
  - model_output_is_not_durable_fact
  - research_is_not_runtime
  - adjacent_dependency_is_not_active_dependency
  - green_ci_is_not_production_authorization
  - derived_l3_is_not_authoritative_fact
  - external_validation_requires_real_external_resource
```

## Active architecture

```yaml
active:
  graph_memory: Graphiti
  graph_backend: Neo4j 5.26 LTS
  canonical_ingest: knowledge/ingest.py
  namespaces:
    normal_recall:
      - personal
      - project
      - knowledge
      - experience
    isolated:
      - imports
  retrieval: separate bounded search per namespace, application-level merge/rank
  chat_persistence: enabled
  provenance_for_new_derived_artifacts: explicit source UUID lineage
  destructive_operations_default: disabled
```

## Bounded / gated architecture

```yaml
bounded:
  promotion:
    mode: deterministic explainability / eligibility gate
    automatic_durable_writer: false
  consolidation:
    mode: DRY_RUN preview
    writes_performed: false
  external_import:
    preview_first: true
    explicit_apply_supported: true
    destination_namespace: imports
    trust_class: untrusted
    normal_chat_recall: false
  provider_e2e:
    requires: OPENAI_API_KEY
    missing_secret_is: BLOCKED_MISSING_SECRET
  legacy_provenance_preview:
    mode: DRY_RUN
    requires:
      - FRACTAL_PROVENANCE_NEO4J_URI
      - FRACTAL_PROVENANCE_NEO4J_USER
      - FRACTAL_PROVENANCE_NEO4J_PASSWORD
```

## Research / non-active paths

Do not infer runtime adoption from documentation or dependencies mentioning:

- GraphRAG;
- KAG;
- CAG;
- PostgreSQL;
- pgvector;
- LadybugDB / Kuzu;
- causal discovery libraries;
- Neo4j GDS write-back;
- alternative model/provider comparisons.

These require separate evidence of activation.

## Ingest authority boundary

Canonical text ingestion must flow through `knowledge/ingest.py`.

Expected high-level sequence:

```text
input text
-> semantic chunks
-> namespace-scoped duplicate check
-> app-owned unique ingest claim
-> Graphiti.add_episode()
-> exact persisted episode UUID
-> exact-UUID metadata/provenance finalization
```

Do not claim that Graphiti's internal transaction and Fractal's post-Graphiti finalization are one database transaction unless code/evidence changes to prove that.

## Retrieval boundary

Do not replace scoped searches with one unscoped global Graphiti search unless explicitly authorized and reviewed.

Cross-namespace `SAME_AS` bridges are not part of the active retrieval authority path.

## Trust boundary

`imports` is isolated and untrusted. High recall count or promotion score must not convert an ineligible origin into trusted authority.

Derived summaries and L3 artifacts may preserve provenance while remaining `authoritative_fact=false`.

## Model policy

Read `core/model_policy.py` before asserting active model defaults.

Do not infer current provider/model support from historical docs. Embedding-model changes require explicit index identity / reindex consideration and must not be coupled silently to chat-model changes.

## Validation semantics

```text
file exists
!= contract tested
!= feature enabled
!= feature on active path
!= runtime observed
!= production authorized
```

Always bind acceptance claims to the exact commit/head that was tested.

Provider-backed E2E must not be reported PASS when the provider secret/resource was absent and the test was skipped.

Legacy provenance migration must remain DRY_RUN unless an explicit owner decision separately authorizes apply.

## Safe modification rules

Before changing memory authority, ingestion, trust, provenance, or retrieval scope:

1. identify the existing canonical path;
2. avoid creating a parallel authority path;
3. preserve namespace and origin boundaries;
4. add/adjust executable contracts;
5. validate exact head;
6. state non-claims explicitly;
7. do not convert research into runtime as a documentation side effect.

## Human-facing docs

Human narrative and visual explanation live in:

- `README.md` — landing page;
- `SYSTEM_OVERVIEW.md` — deep human overview.

Do not copy volatile SHA/run ledgers into those documents unless necessary for a bounded current-state notice. Exact acceptance evidence belongs in PRs, CI, tests, or dedicated status/evidence artifacts.
