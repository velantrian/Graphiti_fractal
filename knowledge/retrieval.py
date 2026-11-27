from __future__ import annotations

from core.config import get_config
from core.erasure_guard import visible_node
from core.memory_import import IMPORT_GROUP_ID


async def search_knowledge(
    graphiti,
    query: str,
    *,
    limit: int = 10,
    group_id: str | None = None,
) -> list[dict]:
    items, _ = await _search_knowledge(graphiti, query, limit=limit, group_id=group_id)
    return items


async def search_knowledge_with_receipt(
    graphiti,
    query: str,
    *,
    user_id: str,
    limit: int = 10,
    group_id: str | None = None,
) -> dict:
    """Same search, plus a server-side read receipt for exactly the items delivered (core/read_receipts.py)."""
    from core.read_receipts import issue_read_receipt

    items, delivered = await _search_knowledge(graphiti, query, limit=limit, group_id=group_id)
    receipt = await issue_read_receipt(graphiti, user_id=user_id, tool="memory.search_knowledge", items=delivered)
    return {"items": items, "read_receipt": receipt}


async def _search_knowledge(graphiti, query: str, *, limit: int, group_id: str | None) -> tuple[list[dict], list[dict]]:
    """Structural knowledge search over Graphiti fulltext indexes without LLM calls.

    External imports are untrusted and therefore excluded from the default search.
    They may be inspected only through an explicit ``group_id="imports"`` request,
    and every returned item remains non-authoritative retrieval context.
    """
    q = (query or "").strip()
    if not q:
        return [], []

    experience_group_id = get_config().memory.experience_group_id
    result = await graphiti.driver.execute_query(
        """
        CALL {
          CALL db.index.fulltext.queryNodes('node_name_and_summary', $q) YIELD node, score
          RETURN node, score, 'Entity' AS kind
          UNION
          CALL db.index.fulltext.queryNodes('episode_content', $q) YIELD node, score
          RETURN node, score, 'Episodic' AS kind
        }
        WITH node, score, kind, node AS n
        WHERE """ + visible_node("node") + """
          AND (node.group_id IS NULL OR node.group_id <> $experience_group_id)
          AND (
            $group_id = $imports_group_id
            OR node.group_id IS NULL
            OR node.group_id <> $imports_group_id
          )
          AND ($group_id IS NULL OR node.group_id = $group_id)
          AND (
            kind <> 'Episodic'
            OR NOT (coalesce(node.source_description,'') IN ['chat_user','chat_bot'])
          )
          AND (
            kind <> 'Entity'
            OR toLower(coalesce(node.name,'')) <> 'unknown'
          )
        RETURN kind,
               node.uuid AS uuid,
               node.group_id AS group_id,
               node.name AS name,
               node.summary AS summary,
               node.content AS content,
               score
        ORDER BY score DESC
        LIMIT $limit
        """,
        q=q,
        experience_group_id=experience_group_id,
        imports_group_id=IMPORT_GROUP_ID,
        group_id=group_id,
        limit=max(1, min(limit, 50)),
    )

    items = []
    delivered = []      # what the client was shown, with the text before the 500-character cut
    for record in result.records:
        text = record.get("summary") or record.get("content") or record.get("name")
        if not text:
            continue
        normalized = str(text).strip()
        if not normalized:
            continue
        if len(normalized) > 500:
            normalized = normalized[:500].rstrip() + "..."
        item_group_id = record.get("group_id")
        delivered.append({"type": "episode" if record["kind"] == "Episodic" else "entity", "uuid": record.get("uuid"),
                          "group_id": item_group_id,
                          "raw_text": str(record.get("summary") or record.get("content") or record.get("name") or "")})
        items.append(
            {
                "kind": record["kind"],
                "uuid": record.get("uuid"),
                "group_id": item_group_id,
                "origin_class": "untrusted" if item_group_id == IMPORT_GROUP_ID else None,
                "authoritative": False,
                "score": record.get("score"),
                "text": normalized,
            }
        )
    return items, delivered
