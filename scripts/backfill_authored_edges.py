raise SystemExit("Legacy mutating script disabled pending owner-reviewed safe migration")


import asyncio
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from core.graphiti_client import get_graphiti_client

async def backfill_authored_edges():
    print("Connecting to Neo4j...")
    try:
        client = get_graphiti_client()
        graphiti = await client.ensure_ready()
        driver = graphiti.driver
        
        user_id = "sergey"
        
        print(f"Starting backfill for user_id='{user_id}'...")
        
        # Count orphans before
        count_query = """
        MATCH (e:Episodic)
        WHERE NOT EXISTS { MATCH (:User)-[:AUTHORED]->(e) }
        RETURN count(e) AS cnt
        """
        result = await driver.execute_query(count_query)
        orphans_before = result.records[0]["cnt"]
        print(f"Orphan episodes found: {orphans_before}")
        
        if orphans_before == 0:
            print("No orphans to backfill.")
            return

        # Perform backfill
        backfill_query = """
        MATCH (e:Episodic)
        WHERE NOT EXISTS { MATCH (:User)-[:AUTHORED]->(e) }
        MERGE (u:User {user_id: $user_id})
        MERGE (u)-[:AUTHORED]->(e)
        RETURN count(e) AS processed
        """
        
        result = await driver.execute_query(backfill_query, user_id=user_id)
        processed = result.records[0]["processed"]
        
        print(f"Backfill complete. Processed {processed} episodes.")
        print(f"Created [:AUTHORED] edges from User({user_id}) to these episodes.")

    except Exception as e:
        print(f"Error during backfill: {e}")

if __name__ == "__main__":
    asyncio.run(backfill_authored_edges())
