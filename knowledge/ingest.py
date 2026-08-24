from __future__ import annotations

import logging
from datetime import datetime, timezone
from time import perf_counter

from core.config import get_config
from core.embeddings import get_embedding
from core.graphiti_client import get_write_semaphore
from core.ingest_atomicity import (
    acquire_ingest_claim,
    finalize_episode_identity,
    mark_ingest_claim_episode_created,
    mark_ingest_claim_failed,
    release_ingest_claim,
)
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


async def episode_exists(
    graphiti,
    fp: str,
    content: str,
    group_id: str | None = None,
) -> bool:
    """Check exact duplicate identity, scoped to group_id when supplied."""
    result = await graphiti.driver.execute_query(
        """
        MATCH (e:Episodic)
        WHERE coalesce(e.deleted, false) = false
          AND ($group_id IS NULL OR e.group_id = $group_id)
          AND (e.fingerprint = $fp OR e.content = $content)
        RETURN e.uuid AS uuid
        LIMIT 1
        """,
        fp=fp,
        content=content,
        group_id=group_id,
    )
    return bool(result.records)


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


async def ingest_text_document(
    graphiti,
    text: str,
    *,
    source_description: str = "uploaded_text",
    user_id: str | None = None,
    job_id: str | None = None,
    group_id: str | None = None,
) -> dict:
    """Canonical Graphiti-native text ingestion path.

    A unique Fractal ingest claim is acquired before Graphiti writes. This closes
    concurrent same-group/fingerprint races at the app boundary. Graphiti still
    owns its internal transaction; after it returns the exact episode UUID,
    fingerprint/group/authorship are finalized atomically in one Cypher query.
    """
    from api.jobs import update_upload_job
    from core.rate_limit_retry import with_rate_limit_retry
    from core.safe_graphiti import filter_graphiti_results

    cleaned = text.strip()
    if not cleaned:
        raise ValueError("text is empty")
    if not group_id:
        group_id = get_config().memory.knowledge_group_id

    chunks = split_into_semantic_chunks(cleaned, max_chunk_size=1500, min_chunk_size=200)
    if not chunks:
        raise ValueError("text produced no ingestible chunks")

    started = perf_counter()
    total_chunks = len(chunks)
    warnings: list[str] = []
    errors: list[str] = []
    added_count = 0
    skipped_count = 0
    reference_time = datetime.now(timezone.utc)
    semaphore = get_write_semaphore()

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

        try:
            if await episode_exists(graphiti, chunk_fp, chunk, group_id=group_id):
                skipped_count += 1
                if job_id:
                    update_upload_job(job_id, processed_chunks=index, stage="ingest")
                continue

            claim_token = await acquire_ingest_claim(
                graphiti,
                group_id=group_id,
                fingerprint=chunk_fp,
            )
            if not claim_token:
                skipped_count += 1
                if job_id:
                    update_upload_job(job_id, processed_chunks=index, stage="ingest")
                continue

            async def write_episode():
                async with semaphore:
                    return await graphiti.add_episode(
                        name=chunk_source[:100],
                        episode_body=chunk,
                        source_description=source_description,
                        reference_time=reference_time,
                        group_id=group_id,
                    )

            episode_result = await with_rate_limit_retry(
                write_episode,
                op_name=f"add_episode:upload:{index}",
                on_rate_limit=on_rate_limit,
            )
            safety = filter_graphiti_results(episode_result)
            if safety["dropped_entities"] or safety["dropped_edges"]:
                warnings.append(
                    f"Chunk {index}: dropped {safety['dropped_entities']} entities "
                    f"and {safety['dropped_edges']} edges"
                )

            episode = getattr(episode_result, "episode", episode_result)
            episode_uuid = (
                episode.get("uuid") if isinstance(episode, dict) else getattr(episode, "uuid", None)
            )
            if not episode_uuid:
                raise RuntimeError("Graphiti add_episode returned no episode UUID")
            episode_uuid = str(episode_uuid)

            await mark_ingest_claim_episode_created(
                graphiti,
                group_id=group_id,
                fingerprint=chunk_fp,
                token=claim_token,
                episode_uuid=episode_uuid,
            )
            await finalize_episode_identity(
                graphiti,
                episode_uuid=episode_uuid,
                group_id=group_id,
                fingerprint=chunk_fp,
                claim_token=claim_token,
                user_id=user_id,
            )

            try:
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
        except Exception as exc:  # noqa: BLE001
            if claim_token:
                try:
                    if episode_uuid:
                        await mark_ingest_claim_failed(
                            graphiti,
                            group_id=group_id,
                            fingerprint=chunk_fp,
                            token=claim_token,
                            error_type=type(exc).__name__,
                        )
                    else:
                        await release_ingest_claim(
                            graphiti,
                            group_id=group_id,
                            fingerprint=chunk_fp,
                            token=claim_token,
                        )
                except Exception:  # preserve original ingest error
                    logger.exception("Failed to settle ingest claim after chunk error")
            logger.exception("Ingest chunk %d/%d failed", index, total_chunks)
            errors.append(f"Chunk {index}: {type(exc).__name__}: {exc}")

        if job_id:
            update_upload_job(
                job_id,
                stage="ingest",
                total_chunks=total_chunks,
                processed_chunks=index,
            )

    elapsed = perf_counter() - started
    if errors and added_count == 0 and skipped_count == 0:
        status = "error"
        stage = "error"
    elif warnings or errors:
        status = "ok"
        stage = "done_with_warnings"
    else:
        status = "ok"
        stage = "done"

    warnings.extend(errors)
    if job_id:
        update_upload_job(
            job_id,
            stage=stage,
            total_chunks=total_chunks,
            processed_chunks=total_chunks,
            profile={"total_time": elapsed},
            warnings=warnings,
        )

    logger.info(
        "Document ingest complete source=%r group=%s added=%d skipped=%d total=%d elapsed=%.3fs",
        source_description,
        group_id,
        added_count,
        skipped_count,
        total_chunks,
        elapsed,
    )
    return {
        "status": status,
        "added": added_count,
        "skipped": skipped_count,
        "chunks": total_chunks,
        "elapsed": elapsed,
        "warnings": warnings,
    }


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
