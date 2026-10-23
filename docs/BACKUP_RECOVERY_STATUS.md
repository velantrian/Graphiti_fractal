# Fractal backup / recovery — evidence status

Status: **INTEGRITY INVENTORY IMPLEMENTED; DATABASE-CONSISTENT BACKUP AND RESTORE NOT VERIFIED**.

The supported tool records SHA-256 hashes for the bytes of a backup set that has **already been produced offline**. It does not create a Neo4j database backup, does not freeze writes, does not export the graph, does not restore any database, and does not certify conversation continuity.

## Operator protocol (not automated)

1. Stop Fractal writers and take a **Neo4j-supported, consistent** backup using instructions matching the deployed Neo4j edition/version. For Community deployments, a properly stopped volume/file copy may be necessary; do not copy a live, changing database directory and claim consistency.
2. Store the resulting complete offline backup, associated metadata and secrets separately in a protected directory. Do not commit any backups or credentials.
3. From the repository root, run `python -m scripts.backup_manifest /secure/offline-backup --create`.
4. On transfer, run `python -m scripts.backup_manifest /secure/offline-backup --verify`.
5. Restore into an **isolated** Neo4j test instance using supported version-specific procedures. Independently compare node, edge, episode, authorship, provenance, group, and temporal counts and a fixed retrieval test set. Only then record RESTORE_PASS for that fixture.

**A successful hash check proves file integrity only**, not consistent creation or recoverability. Keep production restore and cross-assistant portability NOT_VERIFIED until an isolated restore roundtrip has genuinely run.

The inventory intentionally refuses symlinks, duplicate overwrites and unexpected files. Avoid placing the manifest in the image build context.
