import asyncio
import logging
import sys
import os
from datetime import datetime, timezone

# Add parent directory to sys.path to import core
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.graphiti_client import get_graphiti_client

# Setup logging to see our new stats
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("test_linking")

async def test_live_linking():
    client = get_graphiti_client()
    graphiti = await client.ensure_ready()
    
    test_text = "Проект Graphiti использует Neo4j для хранения фрактальной памяти. Это тестовое сообщение."
    
    print(f"\n--- 1. Adding episode to 'testing_layer' ---")
    # We use a unique group_id to see it connecting to 'personal' or 'knowledge'
    result = await graphiti.add_episode(
        name="test_linking_episode",
        episode_body=test_text,
        source_description="manual_test",
        reference_time=datetime.now(timezone.utc),
        group_id="testing_layer"
    )
    
    episode_uuid = getattr(result, 'uuid', None)
    if not episode_uuid and hasattr(result, 'episode'):
        episode_uuid = getattr(result.episode, 'uuid', None)
        
    print(f"Episode created: {episode_uuid}")
    
    print("\n--- 2. Checking for SAME_AS bridges created for 'Graphiti' ---")
    driver = graphiti.driver
    check_query = """
    MATCH (e:Entity {name_norm: 'graphiti', group_id: 'testing_layer'})-[r:SAME_AS]-(other)
    RETURN e.name as name, e.group_id as g1, other.name as other_name, other.group_id as g2
    """
    
    res = await driver.execute_query(check_query)
    if res.records:
        print(f"SUCCESS! Found {len(res.records)} bridges:")
        for r in res.records:
            print(f"  [Bridge] {r['name']}({r['g1']}) <-> {r['other_name']}({r['g2']})")
    else:
        print("No bridges found. This might happen if extraction didn't identify 'Graphiti' as an entity in this run or if it's already linked.")

    print("\n--- 3. Verifying Retrieval Expansion ---")
    from core.memory_ops import MemoryOps
    memory = MemoryOps(graphiti, "sergey")
    
    # Search in a way that should trigger expansion
    # We ask about Graphiti and see if we get info from 'testing_layer' while searching other layers or vice versa
    search_res = await memory.search_memory("Что такое Graphiti?", scopes=["testing_layer"])
    
    expanded = [e for e in search_res.entities if e.get('is_expanded')]
    print(f"Search found {len(search_res.entities)} entities, {len(expanded)} are from expansion.")
    for e in expanded:
        print(f"  [Expanded Entity] {e['name']} from group {e['group_id']}")

if __name__ == "__main__":
    asyncio.run(test_live_linking())
