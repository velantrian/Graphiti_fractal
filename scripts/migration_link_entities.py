raise SystemExit("Legacy mutating script disabled pending owner-reviewed safe migration")

from neo4j import GraphDatabase
import os
from dotenv import load_dotenv

load_dotenv()

uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
user = os.getenv("NEO4J_USER", "neo4j")
password = os.getenv("NEO4J_PASSWORD", "please_change_me")

driver = GraphDatabase.driver(uri, auth=(user, password))

def link_entities():
    with driver.session() as session:
        print("Linking cross-layer entities with SAME_AS...")
        result = session.run("""
            MATCH (e1:Entity), (e2:Entity)
            WHERE e1.name_norm IS NOT NULL
              AND e1.name_norm = e2.name_norm
              AND e1.uuid < e2.uuid
              AND e1.group_id <> e2.group_id
            MERGE (e1)-[r:SAME_AS]->(e2)
            RETURN count(r) as created_count
        """)
        cnt = result.single()["created_count"]
        print(f"Created/Merged {cnt} SAME_AS relationships.")

    driver.close()

if __name__ == "__main__":
    link_entities()
