raise SystemExit("Legacy mutating script disabled pending owner-reviewed safe migration")

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).parent.parent))
load_dotenv()

from core.graphiti_client import get_graphiti_client

async def cleanup():
    client = get_graphiti_client()
    graphiti = await client.ensure_ready()
    driver = graphiti.driver

    print("=== STARTING CLEANUP OF BAD NODES ===")

    # 1. Удаляем Entity без UUID
    q_bad_entities = """
    MATCH (n:Entity)
    WHERE n.uuid IS NULL OR n.name IS NULL
    DETACH DELETE n
    RETURN count(n) as deleted
    """
    res = await driver.execute_query(q_bad_entities)
    deleted = res.records[0]['deleted']
    print(f"Deleted BAD Entities (null uuid/name): {deleted}")

    # 2. Удаляем Episodic без UUID
    q_bad_episodes = """
    MATCH (n:Episodic)
    WHERE n.uuid IS NULL OR n.content IS NULL
    DETACH DELETE n
    RETURN count(n) as deleted
    """
    res = await driver.execute_query(q_bad_episodes)
    deleted = res.records[0]['deleted']
    print(f"Deleted BAD Episodes (null uuid/content): {deleted}")

    # 3. Дополнительно: узлы с "пустыми" строками, если есть
    q_empty_entities = """
    MATCH (n:Entity)
    WHERE n.uuid = "" OR n.name = ""
    DETACH DELETE n
    RETURN count(n) as deleted
    """
    res = await driver.execute_query(q_empty_entities)
    deleted = res.records[0]['deleted']
    print(f"Deleted EMPTY Entities: {deleted}")

    print("=== CLEANUP COMPLETE ===")

if __name__ == "__main__":
    asyncio.run(cleanup())