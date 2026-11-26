# Erasure containment, variant B (scoped, not global)

Replaces #54's global quarantine. Containment follows **lineage**: only objects that can
carry erased text are restricted; groups whose independence lineage shows stay fully usable.

## Model
1. **Phase 1, atomic** (`core.erasure_guard.erase_node`): tombstone (`FractalErasure`) with a lineage
   *seed* (mentioned entities, edges, group, valid_at, hashed tokens, never the text), revision bump,
   soft or hard delete. Status `PENDING_SCOPE`.
2. **Phase 2, idempotent** (`core.erasure_scope.scope_erasure`, runs at once; `scope_pending_erasures`
   recovers after a crash): area by lineage, then purge/mark. Status `SCOPED`.
   * `pure` (only the erased text produced it): deleted.
   * `mixed` (blended with other sources): marked `erasure_quarantine`, rebuilt later.
   * `exposed` (its extraction saw affected text: the 10-episode window **and** every later episode
     that mentions an affected entity, transitively, so chains longer than the window are covered): marked, cleared later.
   * `derived` (chat turns, summaries, L3 profiles reached through `derived_source_ids`,
     `recall_source_ids`, `summarized_turns` to any depth, in any group): marked, kept hidden.
3. **Rebuild** (`rebuild_area`): commits only at an unchanged revision, records evidence per object,
   `RESOLVED` when no Graphiti-derived object of the erasure is still marked. Tombstones are never deleted.

## Who is restricted
| Object | Restricted while / when |
|---|---|
| the erased object | always |
| objects marked by an open erasure | until evidence is recorded (below) |
| derived objects of a group whose tombstone has no computed area (`PENDING_SCOPE`, or created without a seed) | until scoped; other groups unaffected |
| derived-kind episodes (chat/summary/L3) **without recorded lineage** | once any tombstone exists, until the owner sets `lineage_reviewed=true` |
| everything else, including legacy episodes without `origin_class` and every independent group | never |

A legacy `deleted=true` flag restricts only the flagged object. Its derived objects stay visible
until the flag is migrated to a tombstone (`erase_node`); `scripts/erasure_dry_run.py` shows the effect first.

## Two separate decisions
**A. Send context to the model again (new model-assisted writes).**
Per group, automatically: ingest and chat persistence refuse while an open erasure lists the group in
`blocked_groups` (Graphiti-derived objects of that group would be fed back to the model as candidates;
hiding them at read time does not help). The group reopens only when no such object is still marked.
Unresolved objects keep the group paused; this is accepted policy. The previous-episode window given to
`add_episode` excludes erased/quarantined episodes (Graphiti's default window ignores Fractal's flags).
Other groups are never paused.

**B. Show a specific rebuilt object again.** Per object, tombstone kept. A mark is removed only with a
recorded `erasure_evidence` entry `<event>:<kind>:rev<N>:...` of one of two kinds:
* `re_extracted`: **at least one live source states the stored statement exactly**: same direction, same
  relation name, identical text. The comparison is byte for byte: case, punctuation, signs (`-5`, the
  U+2212 minus and `5` are three different statements), negation and whitespace all count; nothing is
  normalized or scored. The extractor is `graphiti_edge_extractor` (Graphiti's own `extract_edges`, one
  episode, no previous episodes); the entry records the input hash, how many live sources were consulted
  and which one confirmed. Every live source is consulted, and an exact confirmation from any of them
  counts whatever a different source says. The edge's text is **never rewritten**. A reworded, opposite or
  merely similar statement, an ambiguous one, a fact only in the reverse direction, or a fact the
  extraction skipped leaves the edge **restricted for reconciliation** (a skipped fact cannot be told from an
  unsupported one; there is no number/date heuristic). An edge with no live source at all is deleted.
* `rebuilt_from_clean_edges`: an entity summary recomputed from the facts of its incident edges, allowed
  only once every incident edge is clean or re-derived. An entity no live source mentions is deleted.

A token screen of the *stored* text is **not** evidence: a paraphrase of erased content carries none of
its tokens. It runs only as a post-check on re-derived text. Without an extractor nothing is reopened
and the group stays paused. Never reopened here: communities (no model-free way to separate the text),
`derived` episodes (chat, summaries, L3: `owner_decision_needed`) and episodes without recorded lineage.

## A write that overlaps an erasure
`add_episode()` reads its context, calls the model and writes derived objects in one long call; an erasure
in the same group that lands meanwhile cannot have scoped objects that did not exist yet. When ingest sees
(at finalization or at completion) an erasure newer than the revision it started at, it refuses to confirm
the write (`unconfirmed`, claim kept), isolates the episode, and marks every entity/edge derived from it as
exposed to each such erasure (`mark_late_write`): the tombstone is reopened and the group paused until the
usual re-derivation. Erasures in other groups do not disturb a running write (per-group fence).

## Ambiguous late writes (reply lost after the write)
The reply of `add_episode()` can be lost after the database already changed (error after commit, timeout, dropped
connection, a process that dies). If an erasure landed meanwhile, the objects the call created or changed are
**unknown**, so they cannot be marked, and the group is not declared safe:

* Before every model-assisted write a durable attempt record exists: the ingest claim (`write_attempted_at`,
  `erasure_revision`) or, for chat, `:FractalChatWriteAttempt` (created before the call, once per episode).
* An attempt that is neither committed nor settled and is **older than an erasure** (its snapshot revision is lower;
  ingest: same group, chat: any group because chat fences globally) pauses model-assisted writes in its group
  (`require_group_writable` reads these records, so the pause holds after a restart) and keeps that erasure's event
  open (`_close_event` counts it; `reopen_for_unsettled_writes` reopens even a RESOLVED one). A rebuild alone cannot
  resume the group; nothing resumes it automatically.
* Known episode UUID (recorded on the claim/attempt): contained with `mark_late_write` and settled, also by the
  recovery sweep after a restart (`scope_pending_erasures` runs it).
* Unknown: only `core.settlement.settle_unsettled_write` ends it, never automatically (see "Manual settlement"
  below). `list_unsettled_writes` shows them (`scripts/erasure_dry_run.py` counts them as `UNSETTLED_WRITE_ATTEMPT`).
* Readers: while such an attempt exists in a group, the group's DERIVED objects (entities, edges, communities,
  non-original episodes) are hidden from every reader, recall for the chat answer included (`blocked_node`): the unknown
  write may have restated erased text in any of them and nothing can be marked. Original episodes written before it stay
  readable. This lasts until settlement.
* Retrying with `add_episode(uuid=...)` is **not** a repair. Observed on graphiti_core 0.29.3 (pinned in
  `tests/integration/test_ambiguous_late_write.py`): an unknown uuid raises `NodeNotFoundError` (nothing written, no
  model call); an existing uuid is a reprocess handle: same node returned, new model calls, derived entities/edges
  created again, stored content and name unchanged.

## Manual settlement
`settle_unsettled_write(kind, key, group_id, outcome, decided_by, ...)`; every refusal returns `{"settled": False,
"refused": code}` after checking, and nothing is isolated, marked or written on a refusal.
* `contained` needs every UUID provably linked to THIS attempt. Recorded UUID: the list must be exactly it. Otherwise
  the group is not proof: the episode is linked by what the attempt recorded before the call (ingest: claim
  fingerprint; chat: SHA-256 of the body) matched with the episode content, created after the attempt began, same group,
  not claimed by another attempt; exactly one such candidate (`no_provable_link`, `ambiguous_candidates`), and the list
  must equal it (`list_mismatch`, `uuid_foreign_group`, `uuid_missing`, `uuid_not_unique`, `bad_uuid_list`).
  An explicit group matching the attempt is required (`group_required`, `group_mismatch`).
* `no_write` needs `decided_by` and an independent `basis_kind` (`provider_request_log`, `graphiti_transaction_log`,
  `operator_attestation`) with `evidence_ref`; "the node is absent now" (`node_absent`) is not accepted; refused while a
  matching episode exists, while a UUID is recorded, and for an attempt that recorded nothing to look for (chat attempts
  from before the body hash existed: their pause stays).
* The attempt must be open and older than `SETTLEMENT_MIN_AGE_SECONDS` (a call still in flight is not a lost one).
  Clock skew between the application and Neo4j can only make a link unprovable (fail-safe).
* Isolation, decision and audit: step 1 is one statement under the erasure lock that re-verifies group and content
  of the exact objects, isolates the episodes, writes the audit node (`ISOLATING`) and claims the attempt for this
  decision hash; step 2 marks exposure (idempotent); step 3 one locked statement settles the attempt and marks the audit
  `COMMITTED`. A crash between 1 and 3 keeps the pause, leaves audit `ISOLATING`; the same decision resumes, another is
  refused (`settlement_decision_conflict`) until `cancel_pending_settlement`. Concurrent callers: one winner; identical
  decisions settle once (`already_settled`).
* Audit (`FractalSettlementAudit`): who, outcome, basis, evidence, uuids, link proof (content hash, attempt key and
  time). `explain_settlement` rebuilds and re-checks the basis from persistent data (it flags a tampered object).

## Closing an event is atomic
`RESOLVED` is decided by one statement under the erasure control lock that counts the remaining marks (and unsettled
writes) and writes the status; registering a late write is one statement under the same lock. A late write that
arrives before the closing statement keeps the event `SCOPED` (group paused); one that arrives after finds a RESOLVED
event and reopens it. The old two-step close lost exactly such a write (reproduced as a control experiment in
`tests/integration/test_atomic_event_close.py`).

## Erasure requests are idempotent
One event per target UUID. `event_id`, `event_revision`, the lineage seed and the status are fixed when the event is
created; a repeat (concurrent on another connection, after the rebuild finished, after physical deletion) finds the
event and rewrites nothing. `event_revision` is the database revision at which that event was created and never
changes; `database_revision` is the current global revision and grows with every NEW event. The response states
`requested_mode` and `performed_mode` (`soft` | `hard` | `none`): `hard` after `soft` deletes the node physically
(`hard_deleted_at` recorded on the event; status and revisions untouched), a repeat of the same mode performs `none`,
an unknown uuid returns `found=false` and creates nothing. Only the creating request runs the scope phase; scoping
holds a lease so a retry or the recovery sweep cannot compute the same area twice.

## Limits
* Re-derivation costs one extractor call per live source episode of an affected edge.
* Entity summaries that a model wrote richer than the edge facts lose that richness when rebuilt.
* The episode of a write that overlapped an erasure stays isolated (`unconfirmed`); an unknown outcome needs an explicit
  `settle_unsettled_write` (no HTTP/MCP endpoint for it yet; a function for the owner to run).
* Unknown-outcome attempts that predate an erasure block their group after that erasure, also old ones that were never
  settled: the dry run lists them (`UNSETTLED_WRITE_ATTEMPT`) so they can be settled before the first erasure.
* While an outcome is unknown, objects of the unknown write that are NOT marked stay readable (they cannot be found);
  only writes (model use) are paused.
* Agent-originated episodes (MCP) carry no lineage to what the agent saw.
* Chat fences on the global revision (it reads every group): an erasure anywhere makes an in-flight turn retry.
  Document ingest fences per group.
* Experience keeps its own conditions and is outside this model.
* Migration of legacy flags and `lineage_reviewed` marking are owner actions; nothing here runs them.
  `scripts/erasure_dry_run.py [--details]` lists, read-only and without content: flagged objects, derived-kind
  episodes without lineage, objects lineage cannot reach (`LINEAGE_GAP`: not restricted by this model, and their
  **content is unknown**: the report never reads it and an erasure cannot find them), tombstones without a seed.
