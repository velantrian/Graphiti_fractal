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
hiding them at read time does not help). The group reopens when no such object is still marked:
pure objects purged, mixed entities rebuilt, exposed objects cleared. The previous-episode window given
to `add_episode` excludes erased/quarantined episodes (Graphiti's default window ignores Fractal's flags).
Other groups are never paused. Mixed edges and communities cannot be separated from erased text without a
model; re-extraction is **not implemented**, so such objects keep their group paused.

**B. Show a specific rebuilt object again.** Per object, tombstone kept. The object's mark is removed
only with a recorded `erasure_evidence` entry `<event>:<kind>:rev<N>:<sha256[:16]>`:
* `content_screen`: none of the erased text's distinctive tokens (tokens also present in other live episodes
  of the group are ignored). Weak evidence: a paraphrase evades it.
* `rebuilt_from_clean_edges`: entity summary recomputed from the facts of clean edges (count recorded).
* never automatic: `derived` episodes (counted in `owner_decision_needed`) and unknown-lineage derived-kind episodes.

## Limits
* The screen proves absence of tokens, not of meaning. Entity summaries that a model wrote richer than
  the edge facts lose that richness when rebuilt.
* Agent-originated episodes (MCP) carry no lineage to what the agent saw.
* Chat fences on the global revision (it reads every group): an erasure anywhere makes an in-flight turn retry.
  Document ingest fences per group.
* Experience keeps its own conditions and is outside this model.
* Migration of legacy flags and `lineage_reviewed` marking are owner actions; nothing here runs them.
