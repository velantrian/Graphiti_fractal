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

## Limits
* Re-derivation costs one extractor call per live source episode of an affected edge.
* Entity summaries that a model wrote richer than the edge facts lose that richness when rebuilt.
* The episode of a write that overlapped an erasure stays isolated (`unconfirmed`); there is no reconciliation tool yet.
* Agent-originated episodes (MCP) carry no lineage to what the agent saw.
* Chat fences on the global revision (it reads every group): an erasure anywhere makes an in-flight turn retry.
  Document ingest fences per group.
* Experience keeps its own conditions and is outside this model.
* Migration of legacy flags and `lineage_reviewed` marking are owner actions; nothing here runs them.
  `scripts/erasure_dry_run.py [--details]` lists, read-only and without content: flagged objects, derived-kind
  episodes without lineage, objects lineage cannot reach (`LINEAGE_GAP`: not restricted by this model, and their
  **content is unknown**: the report never reads it and an erasure cannot find them), tombstones without a seed.
