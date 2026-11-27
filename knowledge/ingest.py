from __future__ import annotations

import asyncio
import logging
import hashlib
from datetime import datetime, timezone
from time import perf_counter

import openai
from graphiti_core.llm_client.errors import RateLimitError as GraphitiRateLimitError

from core.config import get_config
from core.erasure_guard import (
    GroupWriteRestricted, live_previous_episode_uuids, read_erasure_state, require_erasure_revision,
    require_group_writable,
)
from core.embeddings import get_embedding
from core.graphiti_client import get_write_semaphore
from core.ingest_atomicity import (
    VALID_ORIGIN_CLASSES,
    acquire_ingest_claim,
    claim_key,
    finalize_episode_identity,
    mark_ingest_claim_episode_created,
    mark_ingest_claim_failed,
    mark_ingest_claim_write_attempt,
    release_ingest_claim,
    ReadReceiptStale,
)
from core.read_receipts import merge_lineage_into_existing, verify_read_receipt
from core.text_utils import fingerprint, split_into_semantic_chunks

logger = logging.getLogger(__name__)

MEMORY_TYPES = {"personal", "project", "knowledge", "experience"}


async def _resolve_episode_uuid(
    graphiti,
    *,
    episode_uuid: str | None = None,
    content: str | None = None,
    group_id: str | None = None,
) -> str:
    """Resolve one episode exactly or fail closed on ambiguity."""
    if episode_uuid:
        return episode_uuid
    if not content:
        raise ValueError("episode_uuid or content is required")

    query = """
    MATCH (e:Episodic)
    WHERE e.content = $content
      AND ($group_id IS NULL OR e.group_id = $group_id)
      AND coalesce(e.deleted, false) = false
    RETURN e.uuid AS uuid
    LIMIT 2
    """
    result = await graphiti.driver.execute_query(
        query,
        content=content,
        group_id=group_id,
    )
    uuids = [record["uuid"] for record in result.records if record["uuid"]]
    if not uuids:
        raise LookupError("episode not found")
    if len(uuids) != 1:
        raise RuntimeError(
            "episode content lookup is ambiguous; supply episode_uuid instead of mutating by content"
        )
    return str(uuids[0])


class AmbiguousGraphitiWrite(RuntimeError):
    """An add_episode attempt may have stored the chunk; never repeat it automatically."""


class UnconfirmedEpisodeIdentity(RuntimeError):
    """Matching episode exists but its ingest identity is not confirmed."""


async def episode_exists(
    graphiti, fp: str, content: str, group_id: str | None = None, *,
    origin_class: str | None = None, user_id: str | None = None,
    source_description: str | None = None,
) -> bool:
    """Confirm exact content, committed claim, origin and requested authorship.

    Legacy, orphaned and normalization-colliding records require reconciliation,
    not an automatic rewrite or a success receipt.
    """
    result = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic)
        WHERE coalesce(e.deleted, false) = false
          AND coalesce(e.erasure_quarantined,false)=false
          AND NOT EXISTS { MATCH (:FractalErasure {target_uuid:e.uuid}) }
          AND ($group_id IS NULL OR e.group_id = $group_id)
          AND (e.fingerprint = $fp OR e.content = $content)
        OPTIONAL MATCH (c:FractalIngestClaim)
        WHERE c.group_id = e.group_id AND c.fingerprint = e.fingerprint
          AND c.episode_uuid = e.uuid AND c.state = 'COMMITTED'
          AND c.ingest_quality = 'complete'
          AND c.content_sha256 = $content_sha256
          AND c.token = e.ingest_event_id
        WITH e, count(c) AS committed
        RETURN e.uuid AS uuid,
               coalesce(committed = 1 AND e.content = $content AND e.fingerprint = $fp
                AND e.ingest_quality = 'complete'
                AND e.content_sha256 = $content_sha256
                AND ($source_description IS NULL OR e.source_description = $source_description)
                AND e.origin_class IN ['owner','agent_derived','untrusted','system']
                AND ($origin_class IS NULL OR e.origin_class = $origin_class)
                AND ($user_id IS NULL OR EXISTS {
                    MATCH (e)-[:STORED_FOR]->(:User {user_id:$user_id})
                })
                AND (e.origin_class <> 'owner' OR $user_id IS NULL OR EXISTS {
                    MATCH (:User {user_id:$user_id})-[:AUTHORED]->(e)
                }), false) AS confirmed
        ORDER BY confirmed DESC
        LIMIT 2
        """,
        fp=fp, content=content, group_id=group_id,
        origin_class=origin_class, user_id=user_id, source_description=source_description,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )
    if not result.records:
        return False
    # Any competing matching record is an ambiguity, not confirmation.
    if len(result.records) == 1 and result.records[0].get("confirmed") is True:
        return True
    raise UnconfirmedEpisodeIdentity("matching episode identity unconfirmed; reconciliation required")


async def find_similar_episode(
    graphiti,
    vector: list[float],
    threshold: float = 0.95,
    group_id: str | None = None,
) -> str | None:
    """Return one semantically similar episode UUID inside the requested namespace."""
    if not vector:
        return None
    try:
        result = await graphiti.driver.execute_query(
            """
            CALL db.index.vector.queryNodes('fractal_episodic_vector', 5, $vector)
            YIELD node, score
            WHERE score >= $threshold
              AND coalesce(node.deleted, false) = false
              AND ($group_id IS NULL OR node.group_id = $group_id)
            RETURN node.uuid AS uuid, score
            ORDER BY score DESC
            LIMIT 1
            """,
            vector=vector,
            threshold=threshold,
            group_id=group_id,
        )
    except Exception as exc:  # index may be absent in minimal deployments
        logger.debug("Vector duplicate lookup unavailable: %s", type(exc).__name__)
        return None
    return str(result.records[0]["uuid"]) if result.records else None


async def update_last_seen(graphiti, uuid: str, group_id: str) -> None:
    await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic {uuid:$uuid})
        SET e.last_seen_at=$timestamp, e.group_id=$group_id
        """,
        uuid=uuid,
        timestamp=datetime.now(timezone.utc).isoformat(),
        group_id=group_id,
    )


async def set_fingerprint(
    graphiti,
    fp: str,
    content: str | None = None,
    *,
    episode_uuid: str | None = None,
    group_id: str | None = None,
) -> None:
    uuid = await _resolve_episode_uuid(
        graphiti,
        episode_uuid=episode_uuid,
        content=content,
        group_id=group_id,
    )
    await graphiti.driver.execute_query(
        "MATCH (e:Episodic {uuid:$uuid}) SET e.fingerprint=$fingerprint",
        uuid=uuid,
        fingerprint=fp,
    )


async def set_embedding(
    graphiti,
    content: str | None,
    vector: list[float],
    *,
    episode_uuid: str | None = None,
    group_id: str | None = None,
) -> None:
    if not vector:
        raise ValueError("refusing to persist an empty embedding")
    uuid = await _resolve_episode_uuid(
        graphiti,
        episode_uuid=episode_uuid,
        content=content,
        group_id=group_id,
    )
    await graphiti.driver.execute_query(
        "MATCH (e:Episodic {uuid:$uuid}) SET e.embedding=$vector",
        uuid=uuid,
        vector=vector,
    )


async def set_group_id(
    graphiti,
    content: str | None,
    group_id: str,
    *,
    episode_uuid: str | None = None,
) -> None:
    uuid = await _resolve_episode_uuid(
        graphiti,
        episode_uuid=episode_uuid,
        content=content,
        group_id=None,
    )
    await graphiti.driver.execute_query(
        "MATCH (e:Episodic {uuid:$uuid}) SET e.group_id=$group_id",
        uuid=uuid,
        group_id=group_id,
    )


async def link_user(
    graphiti,
    fp: str,
    user_id: str,
    *,
    group_id: str | None = None,
) -> None:
    result = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic {fingerprint:$fingerprint})
        WHERE ($group_id IS NULL OR e.group_id=$group_id)
          AND coalesce(e.deleted, false)=false
        RETURN e.uuid AS uuid
        LIMIT 2
        """,
        fingerprint=fp,
        group_id=group_id,
    )
    uuids = [record["uuid"] for record in result.records]
    if len(uuids) != 1:
        raise RuntimeError("fingerprint does not resolve to exactly one episode")

    from core.authorship import attach_author

    await attach_author(str(uuids[0]), user_id)


def _infer_memory_type(text: str, source_description: str = "") -> str:
    """Heuristic routing only; an explicit memory_type always wins."""
    text_lower = text.lower()
    source_lower = source_description.lower()

    if "personal" in source_lower or "личн" in source_lower:
        return "personal"
    if "project" in source_lower or "проект" in source_lower:
        return "project"
    if "experience" in source_lower or "опыт" in source_lower:
        return "experience"

    personal_keywords = (
        "я ", "мне ", "мой ", "моя ", "мои ", "меня ", "семья", "привычки",
        "отношения", "эмоции",
    )
    project_keywords = (
        "проект", "задача", "разработка", "код", "архитектура", "репозиторий",
        "коммит", "деплой", "документация",
    )
    experience_keywords = (
        "ошибка", "проблема", "решение", "успех", "паттерн", "урок", "опыт",
        "результат", "итог",
    )

    scores = {
        "personal": sum(keyword in text_lower for keyword in personal_keywords),
        "project": sum(keyword in text_lower for keyword in project_keywords),
        "experience": sum(keyword in text_lower for keyword in experience_keywords),
    }
    best_type = max(scores, key=scores.get)
    return best_type if scores[best_type] > 0 else "knowledge"


def resolve_group_id(memory_type: str) -> str:
    if memory_type not in MEMORY_TYPES:
        raise ValueError(f"invalid memory_type: {memory_type!r}")
    config = get_config()
    return {
        "personal": config.memory.personal_group_id,
        "project": config.memory.project_group_id,
        "knowledge": config.memory.knowledge_group_id,
        "experience": config.memory.experience_group_id,
    }[memory_type]


def _get_group_id(memory_type: str) -> str:
    """Backward-compatible alias for resolve_group_id."""
    return resolve_group_id(memory_type)


async def remember_text(
    graphiti,
    text: str,
    *,
    source_description: str = "user_chat",
    user_id: str | None = None,
    memory_type: str | None = None,
) -> dict:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("text is empty")
    routed_type = memory_type or _infer_memory_type(cleaned, source_description)
    return await ingest_text_document(
        graphiti,
        cleaned,
        source_description=source_description,
        user_id=user_id,
        group_id=resolve_group_id(routed_type),
    )


async def _settle_unfinished_claim(graphiti, *, group_id: str, fingerprint: str, token: str,
                                   write_started: bool, error_type: str) -> str:
    """Decide what a claim becomes when its chunk did not finish. Never raises.

    "released": no attempt reached Graphiti (this process never called it and the
        database holds no write-attempt marker), so only this writer's claim is
        deleted, by token, and a retry of the same chunk can proceed.
    "reconciliation": Graphiti may have written (an attempt was made, even one that
        raised a rate limit or timeout); the claim is kept as RECONCILIATION_REQUIRED.
    "pending": settlement itself failed (database unreachable). This writer never
        called Graphiti; the claim stays PENDING and, having no marker, can be
        replaced once its TTL has passed.
    """
    try:
        if not write_started:
            if await release_ingest_claim(graphiti, group_id=group_id, fingerprint=fingerprint,
                                          token=token, confirmed_no_write=True):
                return "released"
            # Not deleted: a write-attempt marker exists (its commit may have been
            # acknowledged ambiguously) or the claim is no longer ours.
        await mark_ingest_claim_failed(graphiti, group_id=group_id, fingerprint=fingerprint,
                                       token=token, error_type=error_type)
        return "reconciliation"
    except Exception:  # noqa: BLE001 - settlement must not mask the original failure
        logger.error("Failed to settle unfinished ingest claim")
        return "reconciliation" if write_started else "pending"


async def _contain_if_erased_meanwhile(graphiti, group_id: str, since_revision: int | None,
                                       episode_uuids: list[str], *, claim_fingerprint: str | None = None) -> None:
    """An erasure in this group landed while add_episode was running: contain what it wrote.

    Known episode UUIDs: their derived objects are marked (and the claim settled). A chunk whose
    write reached Graphiti but whose episode is UNKNOWN (reply lost): nothing can be marked, so the
    erasures it crossed are reopened and held; the claim stays unsettled, which pauses the group
    until an explicit settlement (core.late_writes). Never raises (it runs inside error handling);
    if it fails, the durable claim itself still pauses the group (require_group_writable) and the
    recovery sweep repeats this after a restart.
    """
    if since_revision is None:
        return
    try:
        from core.erasure_scope import mark_late_write
        from core.late_writes import reopen_for_unsettled_writes
        if episode_uuids:
            result = await mark_late_write(graphiti, group_id, since_revision, episode_uuids)
            if result.get("events") and claim_fingerprint:
                await graphiti.driver.execute_query(
                    "MATCH (c:FractalIngestClaim {claim_key:$k}) "
                    "WHERE c.late_settled_at IS NULL AND c.recall_receipt_id IS NULL "
                    "SET c.late_settled_at=datetime(), c.late_settlement='contained'",
                    k=claim_key(group_id, claim_fingerprint))
        elif claim_fingerprint:
            await reopen_for_unsettled_writes(graphiti)
    except Exception:  # noqa: BLE001
        logger.error("Could not contain objects written during an erasure: group paused is not guaranteed")


async def _contain_stale_derivation(graphiti, group_id: str, episode_uuid: str, receipt) -> None:
    """A source of a derived write vanished between the check and finalization: the episode already exists.
    Isolate it and mark everything derived from it as exposed to every erasure since the read (any group)."""
    try:
        from core.erasure_guard import quarantine_unconfirmed_episode
        from core.erasure_scope import mark_late_write
        await quarantine_unconfirmed_episode(graphiti, episode_uuid)
        await mark_late_write(graphiti, group_id, receipt.revision, [episode_uuid], any_group=True)
        # With no newer erasure, the durable recall claim remains the hold.
        # Never invent a tombstone or automatically settle an unconfirmed receipt.
    except Exception:  # noqa: BLE001
        logger.error("Could not contain a derived episode whose source vanished")


async def ingest_text_document(
    graphiti,
    text: str,
    *,
    source_description: str = "uploaded_text",
    user_id: str | None = None,
    job_id: str | None = None,
    group_id: str | None = None,
    origin_class: str | None = None,
    expected_revision: int | None = None,
    read_receipt=None,
) -> dict:
    """Canonical Graphiti-native text ingestion path with durable origin.

    ``read_receipt`` (core.read_receipts.VerifiedReceipt): the write is a retelling of those delivered sources. It is
    verified again before every chunk reaches Graphiti and inside the atomic finalization of every stored chunk, which
    also records the lineage on the episode; a source deleted or changed meanwhile contains the already created episode
    and its derived objects instead of finalizing it.

    A unique Fractal ingest claim is acquired before Graphiti writes. This closes
    concurrent same-group/fingerprint races at the app boundary. Graphiti still
    owns its internal transaction; after it returns the exact episode UUID,
    fingerprint/group/origin/authorship are finalized atomically in one Cypher query.

    When a caller does not state origin explicitly, authenticated owner ingestion
    (``user_id`` present) is classified as ``owner``; anonymous/internal ingestion
    fails closed to ``untrusted``.
    """
    from api.jobs import update_upload_job
    from core.rate_limit_retry import with_rate_limit_retry
    from core.safe_graphiti import filter_graphiti_results

    cleaned = text.strip()
    if not cleaned:
        raise ValueError("text is empty")
    if read_receipt is not None:
        # refused as a whole, before any chunk is claimed or sent: ReadReceiptRejected is not swallowed per chunk
        await verify_read_receipt(graphiti, user_id=user_id, receipt_id=read_receipt.receipt_id,
                                  source_ids=list(read_receipt.source_ids))
    if not group_id:
        group_id = get_config().memory.knowledge_group_id

    resolved_origin_class = origin_class or ("owner" if user_id else "untrusted")
    if resolved_origin_class not in VALID_ORIGIN_CLASSES:
        raise ValueError(f"invalid origin_class: {resolved_origin_class!r}")

    chunks = split_into_semantic_chunks(cleaned, max_chunk_size=1500, min_chunk_size=200)
    if not chunks:
        raise ValueError("text produced no ingestible chunks")

    # Capture once for the entire operation. Queued callers supply the revision
    # captured BEFORE scheduling, not a new revision when the task wakes up.
    if expected_revision is None:
        expected_revision, _ = await read_erasure_state(graphiti)
    else:
        await require_erasure_revision(graphiti, expected_revision, group_id=group_id)
    started = perf_counter()
    total_chunks = len(chunks)
    warnings: list[str] = []
    degraded_chunks = 0
    errors: list[str] = []
    added_count = 0
    skipped_count = 0
    skipped_existing = 0
    skipped_claim_held = 0
    unconfirmed_existing = 0
    unconfirmed_writes = 0
    reference_time = datetime.now(timezone.utc)
    semaphore = get_write_semaphore()
    written_uuids: list[str] = []      # episodes this call made Graphiti create

    if job_id:
        update_upload_job(
            job_id,
            stage="ingest",
            total_chunks=total_chunks,
            processed_chunks=0,
        )

    def on_rate_limit(sleep_seconds: float, attempt: int) -> None:
        if job_id:
            update_upload_job(
                job_id,
                stage="rate_limited",
                message=f"Rate limited; retry in {sleep_seconds:.1f}s",
                retry_in_seconds=sleep_seconds,
                attempt=attempt,
            )

    for index, chunk in enumerate(chunks, start=1):
        chunk_fp = fingerprint(chunk)
        chunk_source = (
            f"{source_description} (part {index}/{total_chunks})"
            if total_chunks > 1
            else source_description
        )
        claim_token: str | None = None
        episode_uuid: str | None = None
        write_started = False
        chunk_uuid_start = len(written_uuids)

        try:
            await require_erasure_revision(graphiti, expected_revision, group_id=group_id)
            await require_group_writable(graphiti, group_id)    # pause only groups with unresolved derived objects
            if await episode_exists(graphiti, chunk_fp, chunk, group_id=group_id,
                                    origin_class=resolved_origin_class, user_id=user_id,
                                    source_description=source_description):
                if read_receipt is not None:
                    if not await merge_lineage_into_existing(graphiti, group_id=group_id, fingerprint=chunk_fp,
                                                              content=chunk, receipt=read_receipt, user_id=user_id):
                        raise UnconfirmedEpisodeIdentity("receipt lineage was not confirmed")
                skipped_count += 1
                skipped_existing += 1
                if job_id:
                    update_upload_job(job_id, processed_chunks=index, stage="ingest")
                continue

            claim_token = await acquire_ingest_claim(
                graphiti,
                group_id=group_id,
                fingerprint=chunk_fp,
                expected_revision=expected_revision,
            )
            if not claim_token:
                # Another writer holds the claim: this call did not confirm the chunk.
                skipped_count += 1
                skipped_claim_held += 1
                if job_id:
                    update_upload_job(job_id, processed_chunks=index, stage="ingest")
                continue

            async def write_episode():
                nonlocal write_started
                async with semaphore:
                    # Durable compare-and-set *before* the call: afterwards "no marker"
                    # proves Graphiti was never called for this claim. Raises
                    # IngestClaimLost (no write) if the claim was replaced meanwhile.
                    await require_erasure_revision(graphiti, expected_revision, group_id=group_id)
                    await require_group_writable(graphiti, group_id)       # re-check right before the call
                    previous = await live_previous_episode_uuids(graphiti, group_id, reference_time)
                    if read_receipt is not None:      # still before the attempt marker: a refusal here proves no write
                        await verify_read_receipt(graphiti, user_id=user_id, receipt_id=read_receipt.receipt_id,
                                                  source_ids=list(read_receipt.source_ids))
                    await mark_ingest_claim_write_attempt(
                        graphiti, group_id=group_id, fingerprint=chunk_fp, token=claim_token,
                        read_receipt=read_receipt, user_id=user_id)
                    if write_started:
                        # A previous attempt already reached Graphiti and its outcome is
                        # unknown: another add_episode could store the chunk twice.
                        raise AmbiguousGraphitiWrite("earlier attempt reached Graphiti; not repeating")
                    write_started = True
                    try:
                        return await graphiti.add_episode(
                            name=chunk_source[:100],
                            episode_body=chunk,
                            source_description=source_description,
                            reference_time=reference_time,
                            group_id=group_id,
                            **({} if previous is None else {"previous_episode_uuids": previous}),
                        )
                    except (openai.RateLimitError, GraphitiRateLimitError) as exc:
                        # Graphiti runs several model calls and database writes inside one
                        # add_episode; a rate-limit error does not prove nothing was stored,
                        # so it must not be retried here (the retry wrapper would call again).
                        raise AmbiguousGraphitiWrite(
                            f"{type(exc).__name__} from add_episode; write outcome unknown") from exc

            episode_result = await with_rate_limit_retry(
                write_episode,
                op_name=f"add_episode:upload:{index}",
                on_rate_limit=on_rate_limit,
            )
            episode = getattr(episode_result, "episode", episode_result)
            episode_uuid = (
                episode.get("uuid") if isinstance(episode, dict) else getattr(episode, "uuid", None)
            )
            if not episode_uuid:
                raise RuntimeError("Graphiti add_episode returned no episode UUID")
            episode_uuid = str(episode_uuid)
            written_uuids.append(episode_uuid)
            await mark_ingest_claim_episode_created(
                graphiti, group_id=group_id, fingerprint=chunk_fp,
                token=claim_token, episode_uuid=episode_uuid,
            )

            safety = filter_graphiti_results(episode_result)
            degraded = safety.get("status") == "DEGRADED_ERROR" or bool(
                safety["dropped_entities"] or safety["dropped_edges"]
            )
            if safety.get("status") == "DEGRADED_ERROR":
                # Validation itself raised: extraction quality is not established.
                warnings.append(
                    f"Chunk {index}: DEGRADED_ERROR — validation raised for "
                    f"{safety.get('errored_entities', 0)} entities and "
                    f"{safety.get('errored_edges', 0)} edges"
                )
            invalid_entities = safety["dropped_entities"] - safety.get("errored_entities", 0)
            invalid_edges = safety["dropped_edges"] - safety.get("errored_edges", 0)
            if invalid_entities or invalid_edges:
                warnings.append(
                    f"Chunk {index}: dropped {invalid_entities} entities "
                    f"and {invalid_edges} edges"
                )

            await finalize_episode_identity(
                graphiti,
                episode_uuid=episode_uuid,
                group_id=group_id,
                fingerprint=chunk_fp,
                claim_token=claim_token,
                user_id=user_id,
                origin_class=resolved_origin_class,
                ingest_quality="degraded" if degraded else "complete",
                content_sha256=hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                read_receipt=read_receipt,
            )
            if degraded:
                degraded_chunks += 1

            try:
                await require_erasure_revision(graphiti, expected_revision, group_id=group_id)
                embedding = await get_embedding(chunk[: get_config().app.max_embedding_chars])
                if embedding is not None:
                    await set_embedding(
                        graphiti,
                        None,
                        embedding,
                        episode_uuid=episode_uuid,
                        group_id=group_id,
                    )
            except Exception as exc:  # embedding is retrieval enhancement, not ingest authority
                logger.warning(
                    "Embedding post-processing failed for chunk %d: %s",
                    index,
                    type(exc).__name__,
                )
                warnings.append(f"Chunk {index}: embedding unavailable")

            added_count += 1
        except UnconfirmedEpisodeIdentity:
            skipped_count += 1
            unconfirmed_existing += 1
            warnings.append(f"Chunk {index}: matching episode identity unconfirmed; reconciliation required")
        except asyncio.CancelledError:
            # Cancelled before the call: release (retry possible). After it: keep for
            # reconciliation. Shielded so settlement completes while cancelled.
            if claim_token:
                await asyncio.shield(_settle_unfinished_claim(
                    graphiti, group_id=group_id, fingerprint=chunk_fp, token=claim_token,
                    write_started=write_started, error_type="CancelledError"))
                if write_started:
                    await asyncio.shield(_contain_if_erased_meanwhile(
                        graphiti, group_id, expected_revision, written_uuids[chunk_uuid_start:],
                        claim_fingerprint=chunk_fp))
            raise
        except Exception as exc:  # noqa: BLE001
            settled = "none"
            if claim_token:
                settled = await _settle_unfinished_claim(
                    graphiti, group_id=group_id, fingerprint=chunk_fp, token=claim_token,
                    write_started=write_started, error_type=type(exc).__name__)
            logger.error("Ingest chunk %d/%d failed: %s", index, total_chunks, type(exc).__name__)
            if isinstance(exc, ReadReceiptStale) and episode_uuid:
                await _contain_stale_derivation(graphiti, group_id, episode_uuid, read_receipt)
            if write_started:
                await _contain_if_erased_meanwhile(graphiti, group_id, expected_revision,
                                                   written_uuids[chunk_uuid_start:], claim_fingerprint=chunk_fp)
            if write_started or settled == "reconciliation":
                # An attempt may have reached Graphiti: neither a failure nor a success.
                unconfirmed_writes += 1
                warnings.append(f"Chunk {index}: {type(exc).__name__}: write outcome unconfirmed")
            else:
                errors.append(f"Chunk {index}: {type(exc).__name__}")

        if job_id:
            update_upload_job(
                job_id,
                stage="ingest",
                total_chunks=total_chunks,
                processed_chunks=index,
            )

    elapsed = perf_counter() - started
    if errors and added_count == 0 and skipped_count == 0 and unconfirmed_writes == 0:
        status = "error"
    else:
        status = "ok"

    warnings.extend(errors)

    logger.info(
        "Document ingest complete source=%r group=%s origin=%s added=%d skipped=%d total=%d elapsed=%.3fs",
        source_description,
        group_id,
        resolved_origin_class,
        added_count,
        skipped_count,
        total_chunks,
        elapsed,
    )
    result = {
        "status": status,
        "added": added_count,
        "skipped": skipped_count,
        "chunks": total_chunks,
        "elapsed": elapsed,
        "warnings": warnings,
        "degraded_chunks": degraded_chunks,
        "failed_chunks": len(errors),
        "skipped_existing": skipped_existing,
        "skipped_claim_held": skipped_claim_held,
        "unconfirmed_existing": unconfirmed_existing,
        "unconfirmed_writes": unconfirmed_writes,
        "completeness": _completeness(total_chunks, added_count, skipped_existing, len(errors),
                                      degraded_chunks, skipped_claim_held, unconfirmed_existing + unconfirmed_writes),
        "origin_class": resolved_origin_class,
    }
    # A receipt must not claim completion if an erasure happened in this group during the
    # operation (including after dedupe or the last chunk's commit).
    result["erasure_revision"] = expected_revision
    try:
        await require_erasure_revision(graphiti, expected_revision, group_id=group_id)
    except Exception:
        await _contain_if_erased_meanwhile(graphiti, group_id, expected_revision, written_uuids)
        result["status"] = "ok"
        result["completeness"] = "unconfirmed"
        result["warnings"].append("Erasure revision could not be confirmed at completion")
    if job_id:
        from api.jobs import finish_upload_job
        finish_upload_job(job_id, result, profile={"total_time": elapsed})
    return result


def _completeness(total: int, added: int, existing: int, failed: int, degraded: int,
                  claim_held: int, unconfirmed_existing: int = 0) -> str:
    """complete: every chunk confirmed stored (written now or already present), none degraded.
    unconfirmed: no chunk definitely failed or degraded here, but some (or all) are held by
    another in-flight writer, so this call can neither confirm nor deny them; also used when
    nothing was confirmed and at least one chunk is held elsewhere. failed: nothing confirmed
    and nothing held elsewhere. partial: some confirmed, some definitely failed/degraded."""
    confirmed = added + existing
    uncertain = claim_held + unconfirmed_existing
    if confirmed == total and not failed and not degraded and not uncertain:
        return "complete"
    if uncertain and (confirmed == 0 or (not failed and not degraded)):
        return "unconfirmed"
    if confirmed == 0:
        return "failed"
    return "partial"


async def ingest_text_document_simple(
    graphiti,
    text: str,
    *,
    source_description: str = "uploaded_text",
    user_id: str | None = None,
) -> dict:
    """Backward-compatible alias; direct Neo4j episode creation has been removed."""
    return await ingest_text_document(
        graphiti,
        text,
        source_description=source_description,
        user_id=user_id,
        group_id=get_config().memory.knowledge_group_id,
    )


async def update_episode_metadata(graphiti, episode_uuid: str, metadata: dict):
    """Update an allow-listed set of episode metadata fields by exact UUID."""
    allowed_fields = {
        "conversation_id",
        "turn_index",
        "episode_kind",
        "is_correction",
        "summarized",
        "covers_turns",
        "summarized_turns",
        "summary_uuid",
    }
    unknown = set(metadata) - allowed_fields
    if unknown:
        raise ValueError(f"unsupported episode metadata fields: {sorted(unknown)}")
    if not metadata:
        return {"status": "unchanged", "episode_uuid": episode_uuid}

    params = {"uuid": episode_uuid, **metadata}
    assignments = ", ".join(f"e.{key} = ${key}" for key in metadata)
    result = await graphiti.driver.execute_query(
        f"MATCH (e:Episodic {{uuid:$uuid}}) SET {assignments} RETURN e.uuid AS uuid",
        **params,
    )
    if not result.records:
        raise LookupError(f"episode not found for metadata update: {episode_uuid}")
    return {"status": "updated", "episode_uuid": episode_uuid}


async def link_user_to_person_entity(
    graphiti,
    user_id: str,
    person_name: str = "Сергей",
):
    """Link the configured User to one matching person Entity."""
    result = await graphiti.driver.execute_query(
        """
        MATCH (u:User {user_id:$user_id})
        MATCH (e:Entity {name:$person_name})
        WITH u, e
        LIMIT 1
        MERGE (u)-[:IS]->(e)
        RETURN u.user_id AS user_id, e.name AS entity_name
        """,
        user_id=user_id,
        person_name=person_name,
    )
    if not result.records:
        return {"status": "not_found", "user_id": user_id, "entity_name": person_name}
    return {"status": "linked", "user_id": user_id, "entity_name": person_name}
