"""Process-local registry for best-effort background tasks.

All application-created asyncio tasks go through this module so shutdown can
wait for or cancel them deterministically instead of leaving orphaned writes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)
_tasks: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


def pending_count() -> int:
    return sum(1 for task in _tasks if not task.done())


async def drain(timeout: float = 30.0) -> tuple[int, int]:
    pending = {task for task in _tasks if not task.done()}
    if not pending:
        return 0, 0

    done, still_pending = await asyncio.wait(pending, timeout=timeout)
    if still_pending:
        logger.warning("Cancelling %d background tasks after %.1fs", len(still_pending), timeout)
        for task in still_pending:
            task.cancel()
        await asyncio.gather(*still_pending, return_exceptions=True)

    return len(done), len(still_pending)
