# Diagnosing an unconfirmed or stuck ingest chunk

Status today: **read-only diagnosis only.** There is no supported repair or
reconciliation tool yet. Do not edit claims by hand.

## What an `unconfirmed` result means

`add_episode()` was called for the chunk and its outcome is unknown (rate limit,
timeout, lost response, crash). Fractal keeps the chunk's claim as
`RECONCILIATION_REQUIRED` (or `PENDING` with a write-attempt marker after a crash)
and will neither call Graphiti again for it nor report it as stored.

Where you see it:

* `POST /remember` and MCP `remember`: `completeness` is `unconfirmed` or
  `partial`, with `unconfirmed_writes` > 0 and a warning
  `Chunk N: ... write outcome unconfirmed`.
* `GET /upload/status/{job_id}`: `stage = done_with_warnings`, `completeness`,
  `counters.unconfirmed_writes`, the same warnings.
* Server log: `Ingest chunk N/M failed: <ExceptionType>`.

## 1. List claims that need attention

```cypher
MATCH (c:FractalIngestClaim)
WHERE c.state = 'RECONCILIATION_REQUIRED'
   OR (c.state = 'PENDING' AND c.expires_at < datetime())
OPTIONAL MATCH (e:Episodic {uuid: c.episode_uuid})
RETURN c.group_id AS group_id, c.fingerprint AS fingerprint, c.state AS state,
       c.error_type AS error_type, c.write_attempts AS write_attempts,
       c.write_attempted_at IS NOT NULL AS attempt_recorded,
       c.episode_uuid AS episode_uuid, e IS NOT NULL AS episode_exists,
       coalesce(c.failed_at, c.created_at) AS since
ORDER BY since
```

* `write_attempts >= 1`: Graphiti was called; the write may or may not exist.
* `attempt_recorded = false` and `state = PENDING` past its TTL: Graphiti was
  never called; the claim is replaced automatically by the next attempt.

## 2. Did the chunk land anyway? (needs the exact chunk text and group)

```cypher
MATCH (e:Episodic {group_id: $group_id})
WHERE e.content = $chunk_text AND coalesce(e.deleted, false) = false
RETURN e.uuid AS uuid, e.fingerprint IS NOT NULL AS finalized,
       e.ingest_quality AS ingest_quality, e.created_at AS created_at
```

* One row, `finalized = false`: Graphiti stored the chunk but the response was
  lost before Fractal could finalize it. Re-sending the same text will **not**
  duplicate it, and will keep returning `unconfirmed` until a reconciliation tool
  exists.
* No row: the write most likely did not land, but this is not proof (Graphiti's
  transaction may still be in flight).

## Under a long rate limit

Every chunk whose `add_episode()` call was rate-limited becomes `unconfirmed`;
later chunks of the same document are still attempted. Re-sending the document
confirms nothing for those chunks (their claims are held) and stores no
duplicates. Changing the text creates a new fingerprint and can duplicate a chunk
that did land, so it is not a safe workaround. Until a reconciliation tool exists,
the supported actions are to wait out the limit before large uploads and to use
the queries above to see how many chunks are affected.
