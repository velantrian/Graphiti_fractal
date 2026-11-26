# Memory integrity — phase 1 (DRAFT / NOT AUTHORIZED FOR MERGE)

Baseline: `92dfabd93d114ae1972c2dc9aca4219515c7da9b`. No production migration or deployment.

MCP writes default to agent-derived origin. Matching content is confirmed only with exact content, a committed claim, matching origin and requested authorship. Uncertain matching records return unconfirmed and require reconciliation. Synthetic retrieval IDs are dropped. Deleted TaskRuns are excluded from both experience queries. Upload status and UI use explicit completeness. Chat uses the configured personal namespace. Six previously omitted regression files join mandatory CI.

Compatibility: legacy episodes without verified claims are not automatically trusted or rewritten. They can now require operator reconciliation. Legacy success summaries without completeness do not confirm saving. No HTTP success alone confirms storage.

Remaining blocking work:
- Cross-process deletion/read fence, Graphiti-native search, derived edges/summaries, caches, clear-all and in-flight writes.
- Durable operation receipts and crash reconciliation; claim TTL/fencing and idempotent retries.
- Full provenance lineage and independent attestation identities, legacy migration and normalized fingerprint collisions.
- Experience atomicity and immutable run identity; explicit observed outcome.
- Scope/configuration checks, retrieval fallback and cache persistence; isolated backup/restore test.

Exact-head CI and independent live Neo4j validation required. Do not run destructive repair on owner data. Do not merge based solely on green CI.
