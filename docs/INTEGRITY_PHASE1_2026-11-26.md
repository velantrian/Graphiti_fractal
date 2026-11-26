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


## Phase 2B — GF-02 (draft; conservative containment)

- Separate durable tombstone and erasure revision committed atomically with target deletion. Known parent TaskRuns quarantined.
- MemoryOps rechecks candidate UUID/kind/namespace after Graphiti search. After erasure or legacy soft deletion, opaque entity/edge/community summaries, unknown-origin and derived episodes are globally quarantined. Explicit original owner/untrusted/system episodes without known derivation markers remain eligible. No automatic quarantine lift; this is an intentional availability trade-off.
- Provider-free live test uses stale Graphiti-shaped candidates against Neo4j; not actual Graphiti provider E2E. Test-only admission fixtures are isolated from the dedicated erasure tests.
- Remaining: all other reader/writer surfaces, RAM, upstream transaction fencing, historic hard-deletes, backups, verified rebuild. Not full erasure safety.

### Phase 2B surface wiring

HTTP/MCP share deletion; local owner buffers cleared. Knowledge, L2/L3, legacy context, conflict diagnosis and visualization respect quarantine. Cached HTTP export denied during quarantine. Online clear-all returns 409 after authorization/confirmation because a distributed write fence is absent. Existing export files/backups are not scrubbed. Other-process RAM and in-flight writes remain unresolved; no full erasure guarantee.

## Phase 2C — durable write fence (draft)

One erasure revision per document; HTTP upload captures it before enqueue, and direct ingest captures it at entry. Claim admission and finalization serialize with deletion on a Neo4j control node. Provider calls check revision before every attempt; a late raw upstream write cannot receive owner classification via an old claim. Ambiguous known episodes are quarantined. End-of-operation revision mismatch is unconfirmed, not complete. This is an application commit fence, not a distributed transaction with Graphiti or proof of physical deletion. Old pending claims without revision require explicit reconciliation; no migration or replay. Tests simulate a late upstream commit in isolated live Neo4j.
