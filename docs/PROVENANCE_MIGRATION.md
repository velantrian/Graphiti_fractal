# 🧾 Provenance Migration Policy

**Status:** bounded migration contract  
**Default mode:** DRY_RUN  
**Authority:** provenance metadata is explanatory/derived, not factual authority.

## New writes

New `chat_summary` artifacts persist deterministic provenance from the exact persisted turn UUIDs used to generate the summary.

New `l3_profile` artifacts persist deterministic provenance from the exact Graphiti L2 community UUIDs used as synthesis context.

Both remain `authoritative_fact=false`.

## Legacy migration

Use:

```bash
python scripts/provenance_migration.py
```

This only previews plans.

Explicit application:

```bash
python scripts/provenance_migration.py --apply
```

Only plans with `status=READY` are mutated.

### Chat summaries

Legacy summaries may be READY when their existing `summarized_turns` contains real source turn UUIDs. The migration derives the deterministic provenance ID from those IDs and the persisted summary payload.

### L3 profiles

Legacy L3 profiles are migrated only when exact `derived_source_ids` already exist. Older profiles without L2 community UUID lineage are:

```text
BLOCKED_MISSING_SOURCE_IDS
```

They are **not** reconstructed from entity names, timestamps, similarity, current communities, or model guesses.

## Invariants

- preview does no writes;
- `--apply` touches READY artifacts only;
- existing `provenance_id` is never overwritten by migration;
- missing lineage fails closed;
- provenance never sets `authoritative_fact=true`;
- migration does not rewrite episode content, temporal validity, Graphiti facts, embeddings, namespaces, or authorship;
- no background/automatic migration is enabled.
