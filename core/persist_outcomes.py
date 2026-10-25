"""Bounded, process-local registry of background write outcomes.

Chat turns and long documents are persisted in background tasks (``spawn``), so the
HTTP response cannot carry the final result. Each write gets a unique ``persist_id``;
its status starts as ``pending`` and is replaced by the final outcome
(persisted / partial / failed / unknown). Callers poll ``GET /persist/{persist_id}``.

Bounded LRU: oldest entries are evicted beyond ``MAX_ENTRIES`` so the map cannot grow
without limit. Evicted/unknown ids read back as ``None`` (endpoint returns 404) —
never as success.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from typing import Any

MAX_ENTRIES = 1000
PERSIST_PENDING = "pending"

_lock = threading.Lock()
_outcomes: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def new_persist_id(kind: str) -> str:
    return f"{kind}:{uuid.uuid4().hex}"


def record(persist_id: str, outcome: dict[str, Any], *, user_id: str | None = None) -> None:
    entry = dict(outcome)
    entry["persist_id"] = persist_id
    entry["updated_at"] = time.time()
    with _lock:
        prev = _outcomes.pop(persist_id, None)
        if user_id is None and prev is not None:
            user_id = prev.get("user_id")
        entry["user_id"] = user_id
        _outcomes[persist_id] = entry
        while len(_outcomes) > MAX_ENTRIES:
            _outcomes.popitem(last=False)


def mark_pending(persist_id: str, *, user_id: str | None) -> None:
    record(persist_id, {"status": PERSIST_PENDING, "episode_uuid": None, "error": None}, user_id=user_id)


def get(persist_id: str, *, user_id: str | None = None) -> dict[str, Any] | None:
    with _lock:
        entry = _outcomes.get(persist_id)
        if entry is None:
            return None
        if user_id is not None and entry.get("user_id") not in (None, user_id):
            return None
        return dict(entry)


def clear() -> None:
    with _lock:
        _outcomes.clear()
