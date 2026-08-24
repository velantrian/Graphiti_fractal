"""Read-only health diagnostics for Fractal Memory."""

from __future__ import annotations

from typing import Any

from core.config import get_config
from core.memory_ops import MemoryOps


async def collect_memory_status(graphiti, *, deep: bool = False) -> dict[str, Any]:
    """Collect bounded diagnostics without changing graph state.

    `deep=True` adds one semantic retrieval probe and can therefore invoke the
    configured embedding/provider path. Neither mode repairs or mutates state.
    """
    config = get_config()
    checks: list[dict[str, Any]] = []

    config_errors = config.validate()
    checks.append(
        {
            "name": "configuration",
            "status": "ok" if not config_errors else "error",
            "details": config_errors,
        }
    )

    try:
        result = await graphiti.driver.execute_query("RETURN 1 AS ok")
        neo4j_ok = bool(result.records and result.records[0]["ok"] == 1)
        checks.append({"name": "neo4j", "status": "ok" if neo4j_ok else "error"})
    except Exception as exc:  # noqa: BLE001
        checks.append(
            {
                "name": "neo4j",
                "status": "error",
                "details": [f"{type(exc).__name__}: {exc}"],
            }
        )

    checks.append(
        {
            "name": "model_policy",
            "status": "ok",
            "details": {
                "chat_model": config.llm.openai_model,
                "graphiti_model": config.llm.graphiti_openai_model,
                "graphiti_small_model": config.llm.graphiti_openai_small_model,
                "embedding_model": config.llm.embedding_model,
                "recall_mode": config.memory.recall_mode,
            },
        }
    )

    if deep:
        try:
            memory = MemoryOps(graphiti, "diagnostic-read-only")
            result = await memory.search_memory(
                "Fractal Memory diagnostic probe",
                scopes=["knowledge"],
                limit=1,
            )
            checks.append(
                {
                    "name": "semantic_retrieval",
                    "status": "ok",
                    "details": {
                        "episodes": result.total_episodes,
                        "entities": result.total_entities,
                        "edges": result.total_edges,
                        "communities": result.total_communities,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                {
                    "name": "semantic_retrieval",
                    "status": "error",
                    "details": [f"{type(exc).__name__}: {exc}"],
                }
            )
    else:
        checks.append(
            {
                "name": "semantic_retrieval",
                "status": "unprobed",
                "details": ["run memory-status --deep to probe provider-backed retrieval"],
            }
        )

    has_error = any(check["status"] == "error" for check in checks)
    return {
        "status": "error" if has_error else "ok",
        "mode": "deep" if deep else "fast",
        "checks": checks,
        "writes_performed": False,
    }
