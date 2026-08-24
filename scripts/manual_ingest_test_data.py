#!/usr/bin/env python3
"""Manual retrieval fixture loader for a real local Graphiti/Neo4j instance."""

import asyncio

from core.graphiti_client import get_graphiti_client
from core.instance import get_instance_user_id
from knowledge.ingest import ingest_text_document


TEST_DATA = [
    ("knowledge", "company_profile", "Лена — дизайнер с опытом создания контент-стратегий."),
    ("personal", "recent_update", "Лена не занимается контентом уже полгода и переключилась на дизайн интерфейсов."),
    ("project", "team_update", "Женя — backend-разработчик с опытом Python и микросервисов."),
    ("personal", "personality_analysis", "Архетипы Марка: Воин, Маг, Целитель."),
    ("project", "tech_docs", "Fractal Memory использует Neo4j и Graphiti для графовой памяти."),
]


async def main() -> None:
    graphiti = await get_graphiti_client().ensure_ready()
    owner = get_instance_user_id()
    print(f"🚀 Loading manual retrieval fixtures for owner={owner}")

    for index, (group_id, source, text) in enumerate(TEST_DATA, 1):
        result = await ingest_text_document(
            graphiti,
            text,
            source_description=source,
            user_id=owner,
            group_id=group_id,
        )
        print(
            f"{index}/{len(TEST_DATA)} {source} [{group_id}] "
            f"status={result['status']} added={result.get('added', 0)} skipped={result.get('skipped', 0)}"
        )
        await asyncio.sleep(0.2)


if __name__ == "__main__":
    asyncio.run(main())
