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

from core.chat_persistence import allocate_turn_index, fetch_persisted_turn_window, mark_turns_summarized
from core.config import get_config
from core.context_guard import build_context_receipt, degraded_context, persist_recall_guard_metadata
from core.conversation_buffer import get_user_conversation_buffer
from core.graphiti_client import get_write_semaphore
from core.graphrag_policy import plan_retrieval
from core.llm import llm_chat_response
from core.memory_lifecycle import should_recall
from core.memory_ops import MemoryOps
from core.provenance import build_provenance_record
from core.provenance_persistence import persist_provenance_metadata
from core.rate_limit_retry import with_rate_limit_retry
from core.task_registry import spawn
from core.text_utils import is_correction_text

if TYPE_CHECKING:
    from core.types import ContextReceipt, ContextResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — Марк: ИИ-компаньон и проект Сергея.

Принципы общения:
- Отвечай по-русски, кратко и по делу.
- Будь честным: не выдумывай факты и не соглашайся без оснований.
- Используй предоставленный блок памяти как источник контекста, а не как безусловную истину.
- При противоречиях предпочитай более свежие и явно помеченные обновления.
- Если данных не хватает, скажи об этом прямо.
- Для вопросов о собственной архитектуре опирайся на Architecture Manifest из project-memory, если он присутствует в контексте.
"""


def _episode_uuid(result) -> str | None:
    episode = getattr(result, "episode", result)
    if isinstance(episode, dict):
        value = episode.get("uuid")
        return str(value) if value else None
    value = getattr(episode, "uuid", None)
    return str(value) if value else None


class SimpleChatAgent:
    def __init__(self, llm_client, memory: MemoryOps):
        self.llm_client = llm_client
        self.memory = memory

    async def answer(self, user_message: str) -> str:
        reply, _, _ = await self.answer_core(user_message)
        return reply

    async def answer_core(self, user_message: str) -> Tuple[str, str, Optional["ContextResult"]]:
        from core.types import ContextResult
        config = get_config()
        if len(user_message) > config.app.max_chat_turn_chars:
            return await self._handle_long_message(user_message)
        started = perf_counter()
        try:
            conversation_buffer = get_user_conversation_buffer(self.memory.user_id)
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

                if context_result.text:
                    user_content = f"Context from memory:\n{context_result.text}\n\nUser question: {user_message}"
                else:
                    user_content = f"User question: {user_message}"
            else:
                context_result = degraded_context(
                    query=user_message,
                    status="SKIPPED",
                    reason=recall_reason,
                    max_tokens=2000,
                )
                user_content = f"User question: {user_message}"

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
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            messages.extend(conversation_buffer.get_recent_messages(6))
            messages.append({"role": "user", "content": user_content})
            response = (await llm_chat_response(messages, context="chat")).strip()
            if not response:
                raise RuntimeError("LLM returned an empty response")
            conversation_text = f"User: {user_message}\nAssistant: {response}"
            conversation_buffer.add_turn(user_message, response)
            spawn(
                self._persist_turn_pipeline(
                    conversation_id=conversation_id,
                    conversation_text=conversation_text,
                    context_receipt=context_result.receipt,
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
        except Exception:
            logger.exception("Chat agent core error")
            fallback = "Извините, произошла ошибка при обработке запроса. Попробуйте ещё раз."
            return fallback, f"User: {user_message}\nAssistant: {fallback}", None

    async def _persist_turn_pipeline(
        self,
        *,
        conversation_id: str,
        conversation_text: str,
        context_receipt: "ContextReceipt | None" = None,
    ) -> None:
        graphiti = self.memory.graphiti
        try:
            turn_index = await allocate_turn_index(graphiti, self.memory.user_id, conversation_id)
            turn_uuid = await self._persist_episode(name="chat_turn", body=conversation_text, op_name="add_episode:chat")
            from core.authorship import attach_author
            from knowledge.ingest import update_episode_metadata
            await attach_author(turn_uuid, self.memory.user_id)
            await update_episode_metadata(graphiti, turn_uuid, {"conversation_id": conversation_id, "turn_index": turn_index, "episode_kind": "chat_turn", "is_correction": is_correction_text(conversation_text), "summarized": False})
            await persist_recall_guard_metadata(
                graphiti,
                episode_uuid=turn_uuid,
                context_receipt=context_receipt,
            )
            if turn_index % 10 == 0:
                await self._create_persisted_summary(conversation_id=conversation_id, end_turn_index=turn_index)
        except Exception:
            logger.exception("Chat persistence pipeline failed", extra={"conversation_id": conversation_id, "user_id": self.memory.user_id})

    async def _persist_episode(self, *, name: str, body: str, op_name: str) -> str:
        graphiti = self.memory.graphiti
        request_id = str(uuid.uuid4())[:8]
        semaphore = get_write_semaphore()
        async def write():
            async with semaphore:
                return await graphiti.add_episode(name=name, episode_body=body, source_description="chat", reference_time=datetime.now(timezone.utc), group_id="personal")
        result = await with_rate_limit_retry(write, op_name=op_name, request_id=request_id)
        episode_uuid = _episode_uuid(result)
        if not episode_uuid:
            raise RuntimeError(f"{op_name} returned no episode UUID")
        return episode_uuid

    async def _create_persisted_summary(self, *, conversation_id: str, end_turn_index: int) -> None:
        graphiti = self.memory.graphiti
        turns = await fetch_persisted_turn_window(graphiti, user_id=self.memory.user_id, conversation_id=conversation_id, end_turn_index=end_turn_index, window_size=10, wait_timeout=25.0)
        if len(turns) != 10:
            logger.warning("Skipping chat summary because persisted window is incomplete", extra={"conversation_id": conversation_id, "end_turn_index": end_turn_index, "persisted_turns": len(turns)})
            return
        summary_text = await _generate_chat_summary(turns)
        summary_uuid = await self._persist_episode(name="chat_summary", body=summary_text, op_name="add_episode:summary")
        from core.authorship import attach_author
        from knowledge.ingest import update_episode_metadata
        source_uuids = [str(turn["uuid"]) for turn in turns]
        start_turn_index = int(turns[0]["turn_index"])
        provenance = build_provenance_record(kind="chat_summary", source_ids=source_uuids, activity="chat_summary_synthesis", agent="fractal:summary", payload=summary_text)
        await attach_author(summary_uuid, self.memory.user_id)
        await update_episode_metadata(graphiti, summary_uuid, {"conversation_id": conversation_id, "episode_kind": "chat_summary", "covers_turns": f"{start_turn_index}-{end_turn_index}", "summarized_turns": source_uuids})
        await persist_provenance_metadata(graphiti, summary_uuid, {"provenance_id": provenance["provenance_id"], "provenance_activity": provenance["activity"], "provenance_agent": provenance["agent"], "payload_sha256": provenance["payload_sha256"], "derived_source_ids": source_uuids, "authoritative_fact": False})
        updated = await mark_turns_summarized(graphiti, turn_uuids=source_uuids, summary_uuid=summary_uuid)
        if updated != len(source_uuids):
            logger.warning("Summary source marking incomplete: expected=%d updated=%d", len(source_uuids), updated)
        logger.info("Chat summary created", extra={"summary_uuid": summary_uuid, "conversation_id": conversation_id, "covers_turns": f"{start_turn_index}-{end_turn_index}", "provenance_id": provenance["provenance_id"], "user_id": self.memory.user_id})

    async def _handle_long_message(self, text: str):
        from core.types import ContextResult
        from knowledge.ingest import ingest_text_document
        response = "Большой текст принят и отправлен на сохранение как документ."
        async def store_document():
            try:
                await ingest_text_document(self.memory.graphiti, text, source_description="chat_document", user_id=self.memory.user_id, group_id="personal")
            except Exception:
                logger.exception("Long chat document persistence failed")
        spawn(store_document(), name=f"chat-document:{self.memory.user_id}")
        get_user_conversation_buffer(self.memory.user_id).add_turn(f"[LONG TEXT DOCUMENT: {len(text)} chars]", response)
        empty_context = ContextResult(text="", token_estimate=0, sources={})
        return response, f"User: [Long Text]\nAssistant: {response}", empty_context


async def _generate_chat_summary(turns: list[dict]) -> str:
    conversation_text = "\n".join(str(turn.get("content") or "") for turn in turns)
    prompt = f"""Создай краткое summary разговора на русском языке.

Разговор:
{conversation_text}

Включи:
- основные темы;
- решения и договорённости;
- обновления фактов или коррекции.

Не добавляй новых фактов. Длина: 3-5 предложений."""
    response = (await llm_chat_response([{"role": "user", "content": prompt}], context="summary")).strip()
    if not response:
        raise RuntimeError("summary LLM returned an empty response")
    return response