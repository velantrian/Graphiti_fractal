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
| derived objects (entities, edges, communities, non-original episodes) of a group with an **unsettled write attempt that crossed an erasure** | until `settle_unsettled_write`; the group's model-assisted writes are paused for as long. Original episodes stay readable; other groups unaffected ("Ambiguous late writes") |
| everything else, including legacy episodes without `origin_class` and every independent group | never |

A legacy `deleted=true` flag restricts only the flagged object. Its derived objects stay visible
until the flag is migrated to a tombstone (`erase_node`); `scripts/erasure_dry_run.py` shows the effect first.

## Two separate decisions
**A. Send context to the model again (new model-assisted writes).**
Per group, automatically: ingest and chat persistence refuse while an open erasure lists the group in
`blocked_groups` (Graphiti-derived objects of that group would be fed back to the model as candidates;
hiding them at read time does not help). The group reopens only when no such object is still marked.
Unresolved objects keep the group paused; this is accepted policy. A write attempt with an unknown outcome that
crossed an erasure also pauses its group, until it is settled ("Ambiguous late writes"). The previous-episode window given to
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

## Text changed between search and admission (D2)
`admit_search_candidates` (one batched statement) compares the text Graphiti returned - taken BEFORE the 8000-character
cut applied for display - with the same fields in Neo4j: episode content; entity name and summary; edge fact and name;
community name and summary. A candidate whose text changed after the search is excluded: its relevance score belongs to
the old text and must not pass to the new one (it is simply absent from this answer; the next search sees the current
text). A candidate without `_raw_text` (other callers) is checked for identity and erasure state only; `_raw_text` is
internal and never part of a result. Cost: `tests/support/measure_d2_admission_cost.py` (admission only, synthetic 1 KB texts,
median over 25 interleaved runs): +1-5 ms at 10-200 candidates, +16-25 ms at 1000 (a 1 MB statement).

## MCP retellings: read receipts and the deletion of their derivatives
**Contract.** `memory.search_knowledge` returns, next to the items, a `read_receipt`: the server's record
(`:FractalReadReceipt`: owner, expiry 1 h, global revision, and per delivered source its type, UUID, group and SHA-256 of
the text delivered, before display truncation). A client that retells what it read calls `memory.remember` /
`memory.upload` with `derived_from: {receipt_id, source_ids}` (optionally `derivation: "retelling"`). The server checks,
before Graphiti is called and again inside the atomic finalization of every stored chunk, that the receipt exists, belongs
to this owner, has not expired, lists every named source, and that each source still exists once, is visible and still has
the delivered text. Refusals (`ReadReceiptRejected.code`): `bad_request`, `unknown_receipt`, `receipt_not_applicable`,
`receipt_expired`, `source_not_in_receipt`, `source_missing`, `source_not_visible`, `source_changed`, `receipt_required`.
The receipt proves that the system SHOWED those sources; it does not prove that the client's text semantically uses them
(the client may name more, which hides more, or fewer, which hides less).
On success the episode carries `recall_source_ids` (+ `recall_derived`, `recall_receipt_id`), the same lineage fields the
built-in chat uses. A source deleted or changed between the check and finalization denies the finalization: the already
created episode is isolated and everything derived from it is marked exposed to the erasures since the read, in any group.
A repeat or a deduplicated derived write keeps and extends the links (union); an owner-authored episode with the same text
is independent and is not touched.

**Area closure.** From every dependent episode found by lineage (any group) the area now follows what Graphiti extracted
from it: entities (`summary`), edges (`fact`) and communities, in the dependent's own group. An object with no independent
live source (neither the erased episode nor itself dependent) is purged with the area; one that an independent episode
also supports is held (hidden, group paused) until a clean rebuild re-derives it from that source - it is never destroyed
together with the dependent one. Identical text or a new UUID is not independence: a second retelling that carries
lineage is dependent too.

**Boundaries (decision for the owner).**
* A client that sends neither `derived_from` nor `derivation` keeps the previous behaviour: origin `agent_derived`, no link
  to what it read; the retelling and its graph survive the deletion of its source (pinned by `test_mcp_lineage_base.py`).
  So do all records written before this change: they have no lineage and cannot be found by it.
* A text declared as a retelling without a verifiable receipt is always refused before Graphiti.
* `FRACTAL_MCP_REQUIRE_READ_RECEIPT=1` refuses EVERY agent write without a verifiable receipt. Consequence: old clients
  fail on `memory.remember` / `memory.upload` until they send `derived_from`, and an agent cannot store a note that is not
  derived from a prior `search_knowledge`. Default: off (compatible path).
* Receipts exist only for `memory.search_knowledge`; other read paths (recall in chat, `search_memory`) issue none. Receipt
  nodes are not purged after expiry yet.
* A legacy retelling of the same fact (no lineage) is indistinguishable from an independent source, so a shared fact can
  be restored from it.

## Limits
* Re-derivation costs one extractor call per live source episode of an affected edge.
* Entity summaries that a model wrote richer than the edge facts lose that richness when rebuilt.
* The episode of a write that overlapped an erasure stays isolated (`unconfirmed`); an unknown outcome needs an explicit
  `settle_unsettled_write` (no HTTP/MCP endpoint for it yet; a function for the owner to run).
* Unknown-outcome attempts that predate an erasure block their group after that erasure, also old ones that were never
  settled: the dry run lists them (`UNSETTLED_WRITE_ATTEMPT`) so they can be settled before the first erasure.
* While an outcome is unknown, the objects of the unknown write cannot be found and marked one by one, so the
  restriction covers the whole affected group: its derived objects (entities, edges, communities, non-original episodes)
  are withheld from every reader, the chat answer included, and its model-assisted writes are paused, both until
  `settle_unsettled_write`. Original (source) episodes stay readable; other groups are unaffected.
* Agent-originated episodes (MCP) carry no lineage to what the agent saw.
* Chat fences on the global revision (it reads every group): an erasure anywhere makes an in-flight turn retry.
  Document ingest fences per group.
* Experience keeps its own conditions and is outside this model.
* Migration of legacy flags and `lineage_reviewed` marking are owner actions; nothing here runs them.
  `scripts/erasure_dry_run.py [--details]` lists, read-only and without content: flagged objects, derived-kind
  episodes without lineage, objects lineage cannot reach (`LINEAGE_GAP`: not restricted by this model, and their
  **content is unknown**: the report never reads it and an erasure cannot find them), tombstones without a seed.


### Receipt-derived write: admission while the upstream outcome is unknown

A receipt-bearing ingest claim records its receipt identity and revision at the durable
write-attempt boundary, before Graphiti is called. While that attempt is pending or requires
reconciliation, ordinary readers hold the destination group's derived graph and non-original
episodes, but not independent original episodes or unrelated groups. New Graphiti ingest/chat
attempts in that group are refused; receipt attempts also refuse other outstanding write
attempts in the group. Only the atomic source check for the claim itself ignores that claim's
temporary hold, and only for validating the declared sources.

A successful atomic finalization checks the receipt row, owner, expiry, exact source type,
identity and text, then commits the claim and the episode's lineage together. An unconfirmed
outcome (including an edit with no erasure event) leaves the hold durable across restart.
No synthetic erasure event is created. Automatic recovery does not settle receipt-bearing
claims. The existing explicit settlement accepts independent no-write evidence; for contained
writes it requires a real erasure newer than the receipt and durable exposure marks. With no
such event, it refuses containment rather than releasing the hold. An owner may explicitly
soft-erase a provably linked rejected output before using event-based settlement/rebuild.
This is intentionally conservative: a dedicated no-erasure clean-rebuild protocol is not
implemented by this change. Event-based rebuild skips affected events while receipt claims
remain unsettled, since temporary invisibility is not proof of absent independent sources.


### Scoped receipt-attempt / erasure relation (PR #64 follow-up)

Before Graphiti is called, the attempt marker stores only the UUIDs, kinds and groups
of the **selected, verified** receipt sources (not every item returned by the search).
Phase 2 of an erasure records its affected identity footprint before purging graph
objects. It includes the seed, affected entities/edges/communities, and the transitive
dependent episodes and their extracted objects across groups. The completed episodic
scope publishes a version; the footprint is retained when the event resolves.

`ingest_erasure_relation` is the shared rule: RELATED (same-destination-group fence
or intersecting source/affected identity or destination exposure), UNRELATED (newer
but complete, disjoint scope), UNKNOWN (pending/legacy/incomplete evidence).
An unsettled receipt always holds its destination graph. RELATED and UNKNOWN hold
the relevant event open; only RELATED is allowed to receive late-write exposure
marks. The existing mutable blocked_groups field is not evidence of a relationship.
Manual containment refuses unknown relationships or missing related events and
rechecks the marked event set under the final settlement lock. Old claims/events
without scope evidence remain conservative; no migration is assumed. The completed
scope version is limited to episodic erasures under the existing variant B policy.
