
import asyncio
import sys
import os
import logging
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.graphiti_client import get_graphiti_client
from core.embeddings import get_embedding
from core.rate_limit_retry import with_rate_limit_retry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("backfill")

async def backfill_embeddings():
    print("=== STARTING BACKFILL OF EPISODIC EMBEDDINGS ===")
    
    graphiti_client = get_graphiti_client()
    graphiti = await graphiti_client.ensure_ready()
    driver = graphiti.driver
    
    batch_size = 10
    total_updated = 0
    total_failed = 0
    
    while True:
        # Find episodes with NULL embedding
        query = """
        MATCH (e:Episodic)
        WHERE e.embedding IS NULL AND coalesce(e.content, '') <> ''
        RETURN e.uuid AS uuid, e.content AS text
        LIMIT $batch
        """
        
        result = await driver.execute_query(query, batch=batch_size)
        records = result.records
        
        if not records:
            print("🎉 No more episodes with NULL embeddings found.")
            break
            
        print(f"Processing batch of {len(records)} episodes...")
        
        batch_updated = 0
        for record in records:
            uuid = record['uuid']
            text = record['text']
            
            try:
                # Generate embedding with retry
                # Truncate text if too long (safe limit for 8k tokens is roughly 24k chars, but let's be safer with 12k chars)
                embed_text = text
                if len(text) > 12000:
                    print(f"    ⚠️ Text too long ({len(text)} chars), truncating to 12000 chars for embedding...")
                    embed_text = text[:12000]

                print(f"  Generating embedding for {uuid} (len={len(embed_text)})...")
                
                async def _gen():
                    return await get_embedding(embed_text)
                
                vec = await with_rate_limit_retry(
                    _gen, 
                    op_name=f"embed_{uuid[:8]}",
                    on_rate_limit=lambda s, a: print(f"    Rate limit hit, sleeping {s:.1f}s (attempt {a})")
                )
                
                # Validation
                if vec is None:
                    print(f"    ❌ Failed: Embedder returned None for {uuid}")
                    total_failed += 1
                    continue
                    
                if len(vec) != 1536:
                    print(f"    ❌ Failed: Invalid dimension {len(vec)} for {uuid}")
                    total_failed += 1
                    continue
                    
                if not isinstance(vec, list):
                    print(f"    ❌ Failed: Not a list for {uuid}")
                    total_failed += 1
                    continue

                # Update DB
                update_query = """
                MATCH (e:Episodic {uuid: $uuid})
                SET e.embedding = $vec
                """
                await driver.execute_query(update_query, uuid=uuid, vec=vec)
                print(f"    ✅ Updated {uuid}")
                total_updated += 1
                batch_updated += 1
                
            except Exception as e:
                print(f"    ❌ Error processing {uuid}: {e}")
                total_failed += 1
        
        # Prevent infinite loop if we can't update any in the batch
        if batch_updated == 0 and len(records) > 0:
             print("⚠️  WARNING: Could not update any episodes in this batch. Aborting to prevent infinite loop.")
             break

        # Small pause between batches to be nice to API
        await asyncio.sleep(0.5)

    print("\n=== BACKFILL COMPLETE ===")
    print(f"Total updated: {total_updated}")
    print(f"Total failed: {total_failed}")
    
    # Final Verification
    final_query = """
    MATCH (e:Episodic) 
    RETURN count(e) AS total, 
           sum(CASE WHEN e.embedding IS NOT NULL THEN 1 ELSE 0 END) AS with_embedding
    """
    res = await driver.execute_query(final_query)
    rec = res.records[0]
    print(f"Final Stats: Total={rec['total']}, With Embedding={rec['with_embedding']}")
    
    if rec['total'] == rec['with_embedding']:
        print("SUCCESS: 100% coverage!")
    else:
        print(f"WARNING: Still missing {rec['total'] - rec['with_embedding']} embeddings.")

if __name__ == "__main__":
    asyncio.run(backfill_embeddings())
