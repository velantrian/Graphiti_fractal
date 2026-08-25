from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core import get_graphiti_client
from core.instance import get_instance_user_id
from core.memory_ops import MemoryOps
from experience.retrieval import get_antipatterns, get_success_patterns
from knowledge.ingest import ingest_text_document, resolve_group_id
from knowledge.retrieval import search_knowledge


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]


TOOLS: list[Tool] = [
    Tool(
        name="memory.search_knowledge",
        description="Поиск по shared Knowledge Memory.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
                "group_id": {"type": ["string", "null"], "default": None},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="memory.search_experience",
        description="Поиск success patterns или antipatterns в Experience Memory.",
        input_schema={
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["success", "antipatterns"], "default": "success"},
                "task_type": {"type": ["string", "null"], "default": None},
                "context_hash": {"type": ["string", "null"], "default": None},
                "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 50},
                "available_tools": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "default": None,
                    "description": "Optional target-environment tool set used to fail closed on inapplicable success patterns.",
                },
                "forbidden_tools": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "default": None,
                    "description": "Optional target-environment deny-list for success-pattern reuse.",
                },
            },
        },
    ),
    Tool(
        name="memory.remember",
        description="Добавить текст в память владельца FRACTAL_USER_ID.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "source_description": {"type": "string", "default": "mcp_remember"},
                "memory_type": {
                    "type": ["string", "null"],
                    "enum": ["personal", "project", "knowledge", "experience", None],
                    "default": None,
                },
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="memory.upload",
        description="Загрузить текстовый документ от владельца FRACTAL_USER_ID.",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "source_description": {"type": "string", "default": "mcp_upload"},
                "memory_type": {
                    "type": "string",
                    "enum": ["personal", "project", "knowledge", "experience"],
                    "default": "knowledge",
                },
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="memory.delete",
        description="Soft-delete узла по uuid. Hard delete требует FRACTAL_ALLOW_HARD_DELETE=1.",
        input_schema={
            "type": "object",
            "properties": {
                "uuid": {"type": "string"},
                "hard": {"type": "boolean", "default": False},
            },
            "required": ["uuid"],
        },
    ),
]

_framing_mode: str | None = None
_should_exit = False
_graphiti = None


def _write(msg: dict[str, Any]) -> None:
    mode = _framing_mode or "lsp"
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    if mode == "ndjson":
        sys.stdout.buffer.write(body + b"\n")
    else:
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
        sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def _read_message() -> dict[str, Any] | None:
    global _framing_mode

    while True:
        first = sys.stdin.buffer.readline()
        if not first:
            return None
        if not first.strip():
            continue

        stripped = first.strip()
        lower = stripped.lower()
        is_header = lower.startswith(b"content-length:") or (
            b":" in stripped and not stripped.startswith((b"{", b"["))
        )

        if is_header:
            headers: dict[str, str] = {}

            def consume(raw: bytes) -> None:
                text = raw.decode("ascii", errors="ignore").strip()
                if ":" in text:
                    key, value = text.split(":", 1)
                    headers[key.strip().lower()] = value.strip()

            consume(first)
            while True:
                line = sys.stdin.buffer.readline()
                if not line:
                    return None
                if not line.strip():
                    break
                consume(line)

            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                continue
            if length <= 0:
                continue

            raw = sys.stdin.buffer.read(length)
            _framing_mode = "lsp"
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                continue

        _framing_mode = _framing_mode or "ndjson"
        try:
            return json.loads(stripped.decode("utf-8"))
        except json.JSONDecodeError:
            continue


async def _get_graphiti():
    global _graphiti
    if _graphiti is None:
        _graphiti = await get_graphiti_client().ensure_ready()
    return _graphiti


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


async def _tool_call(name: str, args: dict[str, Any]) -> Any:
    owner = get_instance_user_id()

    if name == "memory.search_knowledge":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        graphiti = await _get_graphiti()
        return {
            "items": await search_knowledge(
                graphiti,
                query,
                limit=_bounded_int(args.get("limit"), default=10, minimum=1, maximum=50),
                group_id=args.get("group_id"),
            )
        }

    if name == "memory.search_experience":
        mode = str(args.get("mode") or "success")
        if mode not in {"success", "antipatterns"}:
            raise ValueError("mode must be success or antipatterns")
        graphiti = await _get_graphiti()
        kwargs = {
            "task_type": args.get("task_type"),
            "context_hash": args.get("context_hash"),
            "limit": _bounded_int(args.get("limit"), default=5, minimum=1, maximum=50),
        }
        if mode == "success":
            items = await get_success_patterns(
                graphiti,
                **kwargs,
                available_tools=args.get("available_tools"),
                forbidden_tools=args.get("forbidden_tools"),
            )
        else:
            items = await get_antipatterns(graphiti, **kwargs)
        return {"mode": mode, "items": items}

    if name == "memory.remember":
        text = str(args.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        graphiti = await _get_graphiti()
        return await MemoryOps(graphiti, owner).remember_text(
            text,
            memory_type=args.get("memory_type"),
            source_description=str(args.get("source_description") or "mcp_remember"),
        )

    if name == "memory.upload":
        text = str(args.get("text") or "").strip()
        if not text:
            raise ValueError("text is required")
        memory_type = str(args.get("memory_type") or "knowledge")
        if memory_type not in {"personal", "project", "knowledge", "experience"}:
            raise ValueError("invalid memory_type")
        graphiti = await _get_graphiti()
        return await ingest_text_document(
            graphiti,
            text,
            source_description=str(args.get("source_description") or "mcp_upload"),
            user_id=owner,
            group_id=resolve_group_id(memory_type),
        )

    if name == "memory.delete":
        uuid = str(args.get("uuid") or "").strip()
        if not uuid:
            raise ValueError("uuid is required")
        hard = bool(args.get("hard", False))
        if hard and os.getenv("FRACTAL_ALLOW_HARD_DELETE") != "1":
            raise PermissionError("hard delete is disabled; set FRACTAL_ALLOW_HARD_DELETE=1 explicitly")
        graphiti = await _get_graphiti()
        if hard:
            result = await graphiti.driver.execute_query(
                "MATCH (n {uuid:$uuid}) DETACH DELETE n RETURN 1 AS done",
                uuid=uuid,
            )
            return {"deleted": bool(result.records), "mode": "hard"}
        result = await graphiti.driver.execute_query(
            """
            MATCH (n {uuid:$uuid})
            SET n.deleted=true, n.deleted_at=$ts
            RETURN 1 AS done
            """,
            uuid=uuid,
            ts=datetime.now(timezone.utc).isoformat(),
        )
        return {"deleted": bool(result.records), "mode": "soft"}

    raise ValueError(f"Unknown tool: {name}")


def _tools_list_payload() -> list[dict[str, Any]]:
    return [
        {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}
        for tool in TOOLS
    ]


async def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    global _should_exit

    method = msg.get("method")
    req_id = msg.get("id")

    if method == "initialize":
        params = msg.get("params") or {}
        protocol_version = params.get("protocolVersion") or "2024-11-05"
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": protocol_version,
                "serverInfo": {"name": "fractal-memory-mcp", "version": "0.3.0"},
                "capabilities": {"tools": {}},
            },
        }
    if method == "initialized":
        return None
    if method == "shutdown":
        return {"jsonrpc": "2.0", "id": req_id, "result": None}
    if method == "exit":
        _should_exit = True
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": _tools_list_payload()}}
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            output = await _tool_call(params.get("name"), params.get("arguments") or {})
            text = json.dumps(output, ensure_ascii=False, indent=2) if not isinstance(output, str) else output
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}]},
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(exc)},
            }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"ok": True}}
    if req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": "Method not found"},
        }
    return None


def run() -> None:
    async def amain():
        loop = asyncio.get_running_loop()
        while True:
            msg = await loop.run_in_executor(None, _read_message)
            if msg is None:
                break
            response = await handle(msg)
            if response is not None:
                _write(response)
            if _should_exit:
                break

    asyncio.run(amain())


if __name__ == "__main__":
    run()
