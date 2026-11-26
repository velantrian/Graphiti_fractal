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


## Phase 2A — durable quality and safe reconciliation

- COMMITTED and quality are separate. Persist exact SHA-256, internal ingest event identity, and quality on the episode and claim in the same finalization query. Unknown legacy quality does not certify completion. Differing source labels or competing matches cannot be silently collapsed into confirmation. Labels and internal event IDs are not verified source IDs or independent evidence.
- Non-owner ingestion uses STORED_FOR rather than AUTHORED. Owner origin remains caller-classified, not independent proof of authorship. Existing edges and direct chat attribution are not rewritten.
- No timestamp-based automatic claim takeover. Upstream failure after a write starts is unconfirmed; hold the claim and read its durable state with read_ingest_claim. This is per-chunk inspection, not a durable whole-document/chat operation receipt or automatic recovery. Claims may require explicit operator reconciliation indefinitely. Rate-limit retry of upstream operations is still a separate risk.
- Added provider-free live Neo4j tests of quality replay, account association and restart-style claim reads. No owner database accessed.
