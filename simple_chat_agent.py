"""Memory-aware chat agent.

The agent has one response path. Conversation persistence is best-effort and
runs through the shared background-task registry; summaries are built only from
persisted chat_turn episodes with real UUIDs.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from time import perf_counter
from typing import TYPE_CHECKING, Optional, Tuple

from core import persist_outcomes
from core.chat_persistence import allocate_turn_index, fetch_persisted_turn_window, finalize_chat_episode
from core.config import get_config
from core.context_guard import build_context_receipt, degraded_context, recall_guard_fields
from core.conversation_buffer import clear_user_buffer, get_user_conversation_buffer
from core.erasure_guard import (
    ErasureFenceChanged, live_previous_episode_uuids, read_erasure_state, require_erasure_state,
    require_group_writable, quarantine_unconfirmed_episode,
)
from core.graphiti_client import get_write_semaphore
from core.graphrag_policy import plan_retrieval
from core.llm import llm_chat_response
from core.memory_lifecycle import should_recall
from core.memory_ops import MemoryOps
from core.prompt_boundary import MEMORY_DATA_POLICY, SUMMARY_DATA_POLICY, build_memory_user_content, build_summary_user_content
from core.provenance import build_provenance_record
from core.rate_limit_retry import with_rate_limit_retry
from core.task_registry import spawn
from core.text_utils import is_correction_text

if TYPE_CHECKING:
    from core.types import ContextReceipt, ContextResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = f"""Ты — Velan: ИИ-компаньон.

Принципы общения:
- Отвечай по-русски, кратко и по делу.
- Будь честным: не выдумывай факты и не соглашайся без оснований.
- Используй предоставленный блок памяти как источник контекста, а не как безусловную истину.
- {MEMORY_DATA_POLICY}
- При противоречиях предпочитай более свежие и явно помеченные обновления.
- Если данных не хватает, скажи об этом прямо.
- Для вопросов о собственной архитектуре опирайся на Architecture Manifest из project-memory, если он присутствует в контексте.
"""


# Write outcome statuses: a failed save must never look like "nothing happened".
PERSIST_PERSISTED = "persisted"   # episode + metadata written
PERSIST_PARTIAL = "partial"       # episode written, follow-up metadata/summary failed
PERSIST_FAILED = "failed"         # write raised before an episode UUID was confirmed
PERSIST_UNKNOWN = "unknown"       # timeout/cancel during write: outcome not knowable
PERSIST_UNCONFIRMED = "unconfirmed"  # chunks held by another writer: not confirmed by this call


def _outcome(status: str, episode_uuid, exc) -> dict:
    return {"status": status, "episode_uuid": episode_uuid,
            "error": None if exc is None else f"{type(exc).__name__}: {exc}"}


_DOC_COUNTERS = ("chunks", "added", "skipped_existing", "skipped_claim_held", "unconfirmed_existing", "unconfirmed_writes", "failed_chunks", "degraded_chunks")


def _document_outcome(result) -> dict:
    """Map ingest_text_document's summary to a persist status without overstating it."""
    if not isinstance(result, dict):
        return {**_outcome(PERSIST_UNKNOWN, None, None), "error": "ingest returned no summary"}
    completeness = result.get("completeness")
    if completeness is None:  # legacy summary cannot confirm persistence
        status = PERSIST_FAILED if result.get("status") == "error" else PERSIST_UNKNOWN
    else:
        status = {"complete": PERSIST_PERSISTED, "partial": PERSIST_PARTIAL, "failed": PERSIST_FAILED,
                  "unconfirmed": PERSIST_UNCONFIRMED}.get(
            completeness, PERSIST_UNKNOWN)
    outcome = _outcome(status, None, None)
    if status != PERSIST_PERSISTED and result.get("warnings"):
        outcome["error"] = "; ".join(str(w) for w in result["warnings"])[:500]
    outcome["chunks"] = {k: result.get(k) for k in _DOC_COUNTERS if k in result}
    return outcome


def _episode_uuid(result) -> str | None:
    episode = getattr(result, "episode", result)
    if isinstance(episode, dict):
        value = episode.get("uuid")
        return str(value) if value else None
    value = getattr(episode, "uuid", None)
    return str(value) if value else None


class SimpleChatAgent:
    def __init__(self, llm_client, memory: MemoryOps):
        self.last_persist_id: str | None = None
        self.llm_client = llm_client
        self.memory = memory

    async def answer(self, user_message: str) -> str:
        reply, _, _ = await self.answer_core(user_message)
        return reply

    async def answer_core(self, user_message: str) -> Tuple[str, str, Optional["ContextResult"]]:
        from core.types import ContextResult
        config = get_config()
        started = perf_counter()
        try:
            # Read before using RAM, recall, or scheduling any write. If the DB
            # fence is unavailable, no previous conversation is sent to a model.
            expected_state = await read_erasure_state(self.memory.graphiti)
            if len(user_message) > config.app.max_chat_turn_chars:
                return await self._handle_long_message(user_message, expected_state=expected_state)
            conversation_buffer = get_user_conversation_buffer(
                self.memory.user_id, erasure_state=expected_state)
            conversation_id = conversation_buffer.conversation_id
            recall_enabled, recall_reason = should_recall(user_message, config.memory.recall_mode)
            context_result: ContextResult

            if recall_enabled:
                try:
                    context_result = await asyncio.wait_for(
                        self.memory.build_context_for_query(
                            user_message,
                            scopes=["personal", "project", "knowledge", "experience"],
                            max_tokens=2000,
                            include_episodes=True,
                            include_entities=True,
                        ),
                        timeout=config.memory.recall_timeout_seconds,
                    )
                    plan = plan_retrieval(user_message, "auto")
                    context_result.receipt = build_context_receipt(
                        query=user_message,
                        context=context_result,
                        requested_mode=plan.requested_mode.value,
                        effective_mode=plan.effective_mode.value,
                        reason=plan.reason,
                        status="OK",
                        max_tokens=2000,
                    )
                except asyncio.TimeoutError:
                    context_result = degraded_context(
                        query=user_message,
                        status="DEGRADED_TIMEOUT",
                        reason=f"recall exceeded {config.memory.recall_timeout_seconds:.3f}s",
                        max_tokens=2000,
                    )
                    logger.warning(
                        "Memory recall timed out; continuing without memory",
                        extra={"user_id": self.memory.user_id, "conversation_id": conversation_id},
                    )
                except Exception as exc:  # recall failure must not block reply
                    context_result = degraded_context(
                        query=user_message,
                        status="DEGRADED_ERROR",
                        reason=type(exc).__name__,
                        max_tokens=2000,
                    )
                    logger.warning(
                        "Memory recall failed; continuing without memory: %s",
                        type(exc).__name__,
                        extra={"user_id": self.memory.user_id, "conversation_id": conversation_id},
                    )

                user_content = build_memory_user_content(user_message, context_result.text)
            else:
                context_result = degraded_context(
                    query=user_message,
                    status="SKIPPED",
                    reason=recall_reason,
                    max_tokens=2000,
                )
                user_content = build_memory_user_content(user_message)

            logger.debug(
                "Memory recall decision",
                extra={
                    "user_id": self.memory.user_id,
                    "conversation_id": conversation_id,
                    "recall_mode": config.memory.recall_mode,
                    "recall_enabled": recall_enabled,
                    "recall_reason": recall_reason,
                    "context_status": context_result.receipt.status if context_result.receipt else "UNKNOWN",
                },
            )
            await require_erasure_state(self.memory.graphiti, expected_state)
            if get_user_conversation_buffer(self.memory.user_id, erasure_state=expected_state) is not conversation_buffer:
                raise ErasureFenceChanged("conversation buffer was cleared")
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            messages.extend(conversation_buffer.get_recent_messages(6))
            messages.append({"role": "user", "content": user_content})
            response = (
                await llm_chat_response(messages, context="chat", client=self.llm_client)
            ).strip()
            if not response:
                raise RuntimeError("LLM returned an empty response")
            # A provider call already in flight cannot be recalled. Its output
            # must not reach the client, RAM or a persistence task after a fence change.
            await require_erasure_state(self.memory.graphiti, expected_state)
            if get_user_conversation_buffer(self.memory.user_id, erasure_state=expected_state) is not conversation_buffer:
                raise ErasureFenceChanged("conversation buffer was cleared")
            conversation_text = f"User: {user_message}\nAssistant: {response}"
            conversation_buffer.add_turn(user_message, response)
            persist_id = persist_outcomes.new_persist_id("chat")
            persist_outcomes.mark_pending(persist_id, user_id=self.memory.user_id)
            self.last_persist_id = persist_id
            spawn(
                self._persist_turn_pipeline(
                    conversation_id=conversation_id,
                    conversation_text=conversation_text,
                    context_receipt=context_result.receipt,
                    persist_id=persist_id,
                    expected_state=expected_state,
                ),
                name=f"chat-persist:{self.memory.user_id}:{conversation_id}",
            )
            logger.info(
                "Chat answer completed",
                extra={
                    "user_id": self.memory.user_id,
                    "conversation_id": conversation_id,
                    "duration_ms": (perf_counter() - started) * 1000,
                    "memory_recall": recall_enabled,
                    "context_status": context_result.receipt.status if context_result.receipt else "UNKNOWN",
                },
            )
            return response, conversation_text, context_result
        except ErasureFenceChanged:
            clear_user_buffer(self.memory.user_id)
            logger.warning("Chat discarded after erasure fence changed")
            fallback = "Память изменилась во время обработки. Повторите запрос."
            return fallback, f"User: {user_message}\nAssistant: {fallback}", None
        except Exception as exc:
            logger.error("Chat agent core error: %s", type(exc).__name__)
            fallback = "Извините, произошла ошибка при обработке запроса. Попробуйте ещё раз."
            return fallback, f"User: {user_message}\nAssistant: {fallback}", None

    async def _persist_turn_pipeline(
        self,
        *,
        conversation_id: str,
        conversation_text: str,
        expected_state: tuple[int, bool],
        context_receipt: "ContextReceipt | None" = None,
        persist_id: str | None = None,
    ) -> dict:
        graphiti = self.memory.graphiti
        persist_id = persist_id or persist_outcomes.new_persist_id("chat")
        turn_uuid = None
        episode_write_started = False
        try:
            await require_erasure_state(graphiti, expected_state)
            turn_index = await allocate_turn_index(graphiti, self.memory.user_id, conversation_id)
            await require_erasure_state(graphiti, expected_state)
            episode_write_started = True
            turn_uuid = await self._persist_episode(name="chat_turn", body=conversation_text,
                                                    op_name="add_episode:chat", expected_state=expected_state)
            await finalize_chat_episode(
                graphiti, episode_uuid=turn_uuid, user_id=self.memory.user_id,
                group_id=get_config().memory.personal_group_id, expected_state=expected_state,
                metadata={"conversation_id": conversation_id, "turn_index": turn_index,
                          "episode_kind": "chat_turn", "is_correction": is_correction_text(conversation_text),
                          "summarized": False, **recall_guard_fields(context_receipt)},
            )
            # A summary is admitted only if every source turn is still visible (finalize checks
            # each one); an erasure elsewhere does not stop summaries.
            if turn_index % 10 == 0:
                await self._create_persisted_summary(conversation_id=conversation_id, end_turn_index=turn_index,
                                                     expected_state=expected_state)
            await require_erasure_state(graphiti, expected_state)
            outcome = _outcome(PERSIST_PERSISTED, turn_uuid, None)
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            # Before the episode write started nothing was sent -> failed (not unknown).
            if turn_uuid:
                status = PERSIST_PARTIAL
            elif episode_write_started:
                status = PERSIST_UNKNOWN
            else:
                status = PERSIST_FAILED
            outcome = _outcome(status, turn_uuid, exc)
            self._record_persist_outcome(persist_id, outcome, conversation_id)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return outcome
        except ErasureFenceChanged as exc:
            if turn_uuid:
                try:
                    await quarantine_unconfirmed_episode(graphiti, turn_uuid)
                except Exception:
                    logger.error("Could not mark stale chat episode; global quarantine remains")
            outcome = _outcome(PERSIST_UNCONFIRMED if episode_write_started else PERSIST_FAILED, turn_uuid, exc)
        except Exception as exc:
            outcome = _outcome(PERSIST_PARTIAL if turn_uuid else
                               PERSIST_UNKNOWN if episode_write_started else PERSIST_FAILED, turn_uuid, exc)
        self._record_persist_outcome(persist_id, outcome, conversation_id)
        return outcome

    def _record_persist_outcome(self, persist_id: str, outcome: dict, conversation_id: str | None = None) -> None:
        """Single place that publishes a write outcome (registry + log level by status)."""
        persist_outcomes.record(persist_id, outcome, user_id=self.memory.user_id)
        extra = {"conversation_id": conversation_id, "user_id": self.memory.user_id,
                 "persist_id": persist_id, "persist_status": outcome["status"]}
        if outcome["status"] == PERSIST_PERSISTED:
            logger.info("Persistence outcome persisted", extra=extra)
        else:
            logger.error("Persistence outcome %s: %s", outcome["status"], outcome.get("error"), extra=extra)

    async def _persist_episode(self, *, name: str, body: str, op_name: str,
                               expected_state: tuple[int, bool]) -> str:
        graphiti = self.memory.graphiti
        request_id = str(uuid.uuid4())[:8]
        semaphore = get_write_semaphore()
        async def write():
            async with semaphore:
                await require_erasure_state(graphiti, expected_state)
                group = get_config().memory.personal_group_id
                await require_group_writable(graphiti, group)
                now = datetime.now(timezone.utc)
                previous = await live_previous_episode_uuids(graphiti, group, now)
                return await graphiti.add_episode(
                    name=name, episode_body=body, source_description="chat", reference_time=now, group_id=group,
                    **({} if previous is None else {"previous_episode_uuids": previous}))
        result = await with_rate_limit_retry(write, op_name=op_name, request_id=request_id)
        episode_uuid = _episode_uuid(result)
        if not episode_uuid:
            raise RuntimeError(f"{op_name} returned no episode UUID")
        # Raw upstream result is not yet a finalized chat artifact.
        return episode_uuid

    async def _create_persisted_summary(self, *, conversation_id: str, end_turn_index: int,
                                         expected_state: tuple[int, bool]) -> None:
        graphiti = self.memory.graphiti
        await require_erasure_state(graphiti, expected_state)
        turns = await fetch_persisted_turn_window(
            graphiti, user_id=self.memory.user_id, conversation_id=conversation_id,
            end_turn_index=end_turn_index, window_size=10, wait_timeout=25.0,
            expected_revision=expected_state[0],
        )
        if len(turns) != 10 or [turn.get("turn_index") for turn in turns] != list(range(end_turn_index - 9, end_turn_index + 1)) or len({turn.get("uuid") for turn in turns}) != 10:
            logger.warning("Skipping chat summary because persisted window is incomplete")
            return
        await require_erasure_state(graphiti, expected_state)
        summary_text = await _generate_chat_summary(turns)
        await require_erasure_state(graphiti, expected_state)
        summary_uuid = await self._persist_episode(name="chat_summary", body=summary_text,
                                                    op_name="add_episode:summary", expected_state=expected_state)
        source_uuids = [str(turn["uuid"]) for turn in turns]
        start_turn_index = int(turns[0]["turn_index"])
        provenance = build_provenance_record(kind="chat_summary", source_ids=source_uuids,
                                             activity="chat_summary_synthesis", agent="fractal:summary", payload=summary_text)
        try:
            await finalize_chat_episode(
                graphiti, episode_uuid=summary_uuid, user_id=self.memory.user_id,
                group_id=get_config().memory.personal_group_id, expected_state=expected_state,
                metadata={"conversation_id": conversation_id, "episode_kind": "chat_summary",
                          "covers_turns": f"{start_turn_index}-{end_turn_index}",
                          "summarized_turns": source_uuids, "derived_source_ids": source_uuids,
                          "provenance_id": provenance["provenance_id"], "provenance_activity": provenance["activity"],
                          "provenance_agent": provenance["agent"], "payload_sha256": provenance["payload_sha256"]},
            )
            await require_erasure_state(graphiti, expected_state)
        except Exception:
            try:
                await quarantine_unconfirmed_episode(graphiti, summary_uuid)
            except Exception:
                logger.error("Could not mark unconfirmed summary")
            raise
        logger.info("Chat summary created", extra={"summary_uuid": summary_uuid, "conversation_id": conversation_id})

    async def _handle_long_message(self, text: str, *, expected_state: tuple[int, bool] | None = None):
        from core.types import ContextResult
        from knowledge.ingest import ingest_text_document
        if expected_state is None:
            expected_state = await read_erasure_state(self.memory.graphiti)
        await require_erasure_state(self.memory.graphiti, expected_state)
        buffer = get_user_conversation_buffer(self.memory.user_id, erasure_state=expected_state)
        response = "Большой текст поставлен в очередь на сохранение. Подтверждение записи ещё не получено."
        doc_persist_id = persist_outcomes.new_persist_id("document")
        persist_outcomes.mark_pending(doc_persist_id, user_id=self.memory.user_id)
        self.last_persist_id = doc_persist_id
        async def store_document():
            try:
                await require_erasure_state(self.memory.graphiti, expected_state)
                result = await ingest_text_document(self.memory.graphiti, text, source_description="chat_document",
                                                    user_id=self.memory.user_id, group_id=get_config().memory.personal_group_id,
                                                    expected_revision=expected_state[0])
                await require_erasure_state(self.memory.graphiti, expected_state)
                outcome = _document_outcome(result)
            except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                self._record_persist_outcome(doc_persist_id, _outcome(PERSIST_UNKNOWN, None, exc))
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return
            except ErasureFenceChanged as exc:
                outcome = _outcome(PERSIST_UNCONFIRMED, None, exc)
            except Exception as exc:
                logger.error("Long chat document persistence failed: %s", type(exc).__name__)
                outcome = _outcome(PERSIST_FAILED, None, exc)
            self._record_persist_outcome(doc_persist_id, outcome)
        spawn(store_document(), name=f"chat-document:{self.memory.user_id}")
        buffer.add_turn(f"[LONG TEXT DOCUMENT: {len(text)} chars]", response)
        empty_context = ContextResult(text="", token_estimate=0, sources={})
        return response, f"User: [Long Text]\nAssistant: {response}", empty_context


async def _generate_chat_summary(turns: list[dict]) -> str:
    conversation_text = "\n".join(str(turn.get("content") or "") for turn in turns)
    prompt = build_summary_user_content(conversation_text)
    messages = [
        {"role": "system", "content": SUMMARY_DATA_POLICY},
        {"role": "user", "content": prompt},
    ]
    response = (await llm_chat_response(messages, context="summary")).strip()
    if not response:
        raise RuntimeError("summary LLM returned an empty response")
    return response
