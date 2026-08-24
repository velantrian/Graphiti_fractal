"""High-level memory service built on one Graphiti ingest and retrieval contract."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF
from graphiti_core.search.search_filters import ComparisonOperator, DateFilter, SearchFilters

from core.datetime_utils import dt_to_iso, normalize_dt
from core.graphrag_policy import apply_mode_weights, plan_retrieval
from core.recall_telemetry import record_recall
from core.text_utils import is_correction_text
from core.types import ContextResult, SearchResult
from experience.writer import ingest_experience
from knowledge.ingest import (
    MEMORY_TYPES,
    _infer_memory_type,
    ingest_text_document,
    resolve_group_id,
)

logger = logging.getLogger(__name__)
_recent_memories: dict[str, deque] = {}


def clear_recent_memories(user_id: str) -> int:
    memories = _recent_memories.pop(user_id, None)
    return len(memories) if memories is not None else 0


def _score(scores: list, index: int, default: float) -> float:
    if index < len(scores):
        try:
            return float(scores[index])
        except (TypeError, ValueError):
            pass
    return default


def _identity(item: Any, *, prefix: str, scope: str, index: int) -> str:
    value = getattr(item, "uuid", None)
    return str(value) if value else f"{prefix}:{scope}:{index}"


def _merge_best(target: dict[str, dict], item: dict) -> None:
    key = item["uuid"]
    current = target.get(key)
    if current is None or item.get("score", 0.0) > current.get("score", 0.0):
        target[key] = item


class MemoryOps:
    """Single memory service for ingestion, scoped search, and context building."""

    def __init__(self, graphiti, user_id: str):
        if graphiti is None:
            raise ValueError("graphiti is required")
        if not user_id:
            raise ValueError("user_id is required")
        self.graphiti = graphiti
        self.user_id = user_id

    async def ingest_pipeline(
        self,
        text: str,
        *,
        source_description: str = "ingest_pipeline",
        memory_type: str = "knowledge",
        group_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Delegate to the canonical Graphiti-native ingestion path."""
        target_group = group_id or resolve_group_id(memory_type)
        return await ingest_text_document(
            self.graphiti,
            text,
            source_description=source_description,
            user_id=self.user_id,
            group_id=target_group,
        )

    async def remember_text(
        self,
        text: str,
        *,
        memory_type: Optional[str] = None,
        source_description: Optional[str] = None,
    ) -> Dict[str, Any]:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("text is empty")

        routed_type = memory_type or _infer_memory_type(cleaned, source_description or "")
        if routed_type not in MEMORY_TYPES:
            raise ValueError(f"invalid memory_type: {routed_type!r}")

        memories = _recent_memories.setdefault(self.user_id, deque(maxlen=20))
        memories.append(
            {
                "text": cleaned,
                "memory_type": routed_type,
                "source_description": source_description,
                "timestamp": datetime.now().isoformat(),
            }
        )
        return await self.ingest_pipeline(
            cleaned,
            source_description=source_description or "memory_ops",
            memory_type=routed_type,
        )

    async def remember_experience(
        self,
        experience_data: Dict[str, Any],
        source_description: Optional[str] = None,
    ) -> Dict[str, Any]:
        from experience.models import ExperienceIngestRequest

        request = (
            ExperienceIngestRequest(**experience_data)
            if isinstance(experience_data, dict)
            else experience_data
        )
        return await ingest_experience(self.graphiti, request)

    def _resolve_scopes(self, scopes: Optional[List[str]]) -> list[str]:
        requested = scopes or ["personal", "project", "knowledge", "experience"]
        resolved = []
        for scope in requested:
            group_id = resolve_group_id(scope) if scope in MEMORY_TYPES else scope
            if group_id and group_id not in resolved:
                resolved.append(group_id)
        return resolved

    @staticmethod
    def _temporal_filter(as_of: Optional[datetime]) -> SearchFilters:
        if as_of is None:
            return SearchFilters(
                invalid_at=[[
                    DateFilter(date=None, comparison_operator=ComparisonOperator.is_null)
                ]]
            )
        return SearchFilters(
            valid_at=[[
                DateFilter(date=as_of, comparison_operator=ComparisonOperator.less_than_equal)
            ]],
            invalid_at=[
                [DateFilter(date=None, comparison_operator=ComparisonOperator.is_null)],
                [DateFilter(date=as_of, comparison_operator=ComparisonOperator.greater_than)],
            ],
        )

    async def _search_scope(self, query: str, scope: str, search_filter: SearchFilters):
        return await self.graphiti.search_(
            query=query,
            config=COMBINED_HYBRID_SEARCH_RRF,
            group_ids=[scope],
            search_filter=search_filter,
        )

    async def search_memory(
        self,
        query: str,
        *,
        scopes: Optional[List[str]] = None,
        limit: int = 10,
        include_episodes: bool = True,
        include_entities: bool = True,
        as_of: Optional[datetime] = None,
        retrieval_mode: str = "auto",
    ) -> SearchResult:
        """Search each namespace independently, merge, then apply read-side intent.

        Cross-namespace retrieval does not create or traverse SAME_AS bridges.
        Each Graphiti call is explicitly bound to one group_id. GraphRAG-inspired
        LOCAL/GLOBAL/DRIFT behavior is applied only after this canonical search;
        it never creates a second retrieval or persistence authority.
        """
        if limit < 1:
            raise ValueError("limit must be >= 1")
        query = query.strip()
        if not query:
            return SearchResult()

        plan = plan_retrieval(query, retrieval_mode)
        resolved_scopes = self._resolve_scopes(scopes)
        temporal_filter = self._temporal_filter(as_of)
        raw_results = await asyncio.gather(
            *(
                self._search_scope(query, scope, temporal_filter)
                for scope in resolved_scopes
            ),
            return_exceptions=True,
        )

        episodes: dict[str, dict] = {}
        entities: dict[str, dict] = {}
        edges: dict[str, dict] = {}
        communities: dict[str, dict] = {}

        for scope, result in zip(resolved_scopes, raw_results):
            if isinstance(result, Exception):
                logger.warning("Search failed for scope=%s: %s", scope, type(result).__name__)
                continue

            raw_episodes = getattr(result, "episodes", []) or []
            episode_scores = getattr(result, "episode_reranker_scores", []) or []
            if include_episodes:
                for index, episode in enumerate(raw_episodes):
                    content = (
                        getattr(episode, "content", None)
                        or getattr(episode, "summary", None)
                        or ""
                    )
                    if not str(content).strip():
                        continue
                    episode_kind = getattr(episode, "episode_kind", "") or ""
                    source_description = getattr(episode, "source_description", "") or ""
                    score = _score(episode_scores, index, 0.6)
                    if episode_kind == "chat_turn" or source_description == "chat":
                        score *= 0.3
                    if episode_kind == "chat_summary":
                        score *= 1.3
                    correction = is_correction_text(str(content))
                    if correction:
                        score += 0.5
                    created = normalize_dt(getattr(episode, "created_at", None))
                    item = {
                        "uuid": _identity(episode, prefix="episode", scope=scope, index=index),
                        "content": str(content)[:8000],
                        "name": getattr(episode, "name", "Episode") or "Episode",
                        "score": score,
                        "type": "episode",
                        "group_id": getattr(episode, "group_id", None) or scope,
                        "is_correction": correction,
                        "episode_kind": episode_kind,
                        "source_description": source_description,
                        "created_at": dt_to_iso(created),
                    }
                    _merge_best(episodes, item)

            raw_nodes = getattr(result, "nodes", []) or []
            node_scores = getattr(result, "node_reranker_scores", []) or []
            if include_entities:
                for index, node in enumerate(raw_nodes):
                    item = {
                        "uuid": _identity(node, prefix="entity", scope=scope, index=index),
                        "name": getattr(node, "name", "") or "",
                        "summary": getattr(node, "summary", "") or "",
                        "score": _score(node_scores, index, 0.7),
                        "type": "entity",
                        "group_id": getattr(node, "group_id", None) or scope,
                    }
                    _merge_best(entities, item)

            raw_edges = getattr(result, "edges", []) or []
            edge_scores = getattr(result, "edge_reranker_scores", []) or []
            for index, edge in enumerate(raw_edges):
                item = {
                    "uuid": _identity(edge, prefix="edge", scope=scope, index=index),
                    "fact": getattr(edge, "fact", "") or "",
                    "subject": getattr(edge, "subject", None)
                    or getattr(edge, "source_name", None),
                    "object": getattr(edge, "object", None)
                    or getattr(edge, "target_name", None),
                    "relationship_type": getattr(edge, "relationship_type", None)
                    or getattr(edge, "type", None)
                    or getattr(edge, "rel_type", None),
                    "name": getattr(edge, "name", None),
                    "score": _score(edge_scores, index, 0.5),
                    "type": "edge",
                    "group_id": getattr(edge, "group_id", None) or scope,
                }
                _merge_best(edges, item)

            raw_communities = getattr(result, "communities", []) or []
            community_scores = getattr(result, "community_reranker_scores", []) or []
            for index, community in enumerate(raw_communities):
                item = {
                    "uuid": _identity(community, prefix="community", scope=scope, index=index),
                    "name": getattr(community, "name", "") or "",
                    "summary": getattr(community, "summary", "") or "",
                    "score": _score(community_scores, index, 0.4),
                    "type": "community",
                    "group_id": getattr(community, "group_id", None) or scope,
                }
                _merge_best(communities, item)

        def ranked(values: dict[str, dict]) -> list[dict]:
            return sorted(
                values.values(),
                key=lambda item: item.get("score", 0.0),
                reverse=True,
            )[:limit]

        result = SearchResult(
            episodes=ranked(episodes),
            entities=ranked(entities),
            edges=ranked(edges),
            communities=ranked(communities),
        )
        result.total_episodes = len(result.episodes)
        result.total_entities = len(result.entities)
        result.total_edges = len(result.edges)
        result.total_communities = len(result.communities)
        apply_mode_weights(result, plan)
        logger.debug(
            "GraphRAG retrieval mode applied",
            extra={
                "requested_mode": plan.requested_mode.value,
                "effective_mode": plan.effective_mode.value,
                "reason": plan.reason,
                "scopes": resolved_scopes,
            },
        )
        return result

    async def build_context_for_query(
        self,
        query: str,
        *,
        scopes: Optional[List[str]] = None,
        max_tokens: int = 4000,
        include_episodes: bool = True,
        include_entities: bool = True,
        as_of: Optional[datetime] = None,
        retrieval_mode: str = "auto",
    ) -> ContextResult:
        if max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")

        result = await self.search_memory(
            query,
            scopes=scopes,
            limit=10,
            include_episodes=include_episodes,
            include_entities=include_entities,
            as_of=as_of,
            retrieval_mode=retrieval_mode,
        )

        retrieved_ids = [
            item["uuid"]
            for collection in (result.episodes, result.entities, result.edges, result.communities)
            for item in collection
            if item.get("uuid")
        ]
        if retrieved_ids:
            try:
                await record_recall(
                    self.graphiti,
                    user_id=self.user_id,
                    query=query,
                    object_uuids=retrieved_ids,
                )
            except Exception as exc:  # telemetry must never break retrieval
                logger.warning("Recall telemetry failed closed from answer path: %s", type(exc).__name__)

        sections: list[str] = []
        sources = {"episodes": 0, "entities": 0, "edges": 0, "communities": 0}

        useful_episodes = []
        for episode in result.episodes:
            kind = episode.get("episode_kind", "")
            if kind == "chat_turn" and not episode.get("is_correction", False):
                continue
            content = (episode.get("content") or "").strip()
            if not content:
                continue
            label = "Предыдущие обсуждения" if kind == "chat_summary" else "Память"
            if episode.get("is_correction"):
                label = "Обновление"
            useful_episodes.append(f"- {label}: {content[:600]}")
            if len(useful_episodes) >= 4:
                break
        if useful_episodes:
            sections.append("## Эпизоды\n" + "\n".join(useful_episodes))
            sources["episodes"] = len(useful_episodes)

        entity_lines = []
        for entity in result.entities[:5]:
            name = (entity.get("name") or "").strip()
            if not name:
                continue
            summary = (entity.get("summary") or "").strip()
            entity_lines.append(f"- {name}" + (f": {summary[:300]}" if summary else ""))
        if entity_lines:
            sections.append("## Сущности\n" + "\n".join(entity_lines))
            sources["entities"] = len(entity_lines)

        fact_lines = []
        for edge in result.edges[:8]:
            subject = edge.get("subject")
            target = edge.get("object")
            relation = edge.get("relationship_type")
            fact = (edge.get("fact") or "").strip()
            if subject and target and relation:
                fact_lines.append(f"- {subject} — {relation} → {target}")
            elif fact:
                fact_lines.append(f"- {fact[:300]}")
        if fact_lines:
            sections.append("## Связи и факты\n" + "\n".join(fact_lines))
            sources["edges"] = len(fact_lines)

        community_lines = []
        for community in result.communities[:3]:
            name = (community.get("name") or "").strip()
            summary = (community.get("summary") or "").strip()
            if name or summary:
                community_lines.append(
                    f"- {name or 'Community'}" + (f": {summary[:240]}" if summary else "")
                )
        if community_lines:
            sections.append("## Сообщества\n" + "\n".join(community_lines))
            sources["communities"] = len(community_lines)

        text = "\n\n".join(sections)
        max_chars = max_tokens * 4
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "\n[Контекст обрезан по лимиту]"
        token_estimate = min(max_tokens, (len(text) + 3) // 4)
        return ContextResult(text=text, token_estimate=token_estimate, sources=sources)
