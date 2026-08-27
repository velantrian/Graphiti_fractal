import sys
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime
import asyncio

# --- Mocks for Graphiti Core before import ---
# Мы должны создать моки до того, как они будут импортированы в тестируемых модулях
sys.modules["graphiti_core"] = MagicMock()
sys.modules["graphiti_core.utils"] = MagicMock()
sys.modules["graphiti_core.utils.bulk_utils"] = MagicMock()
sys.modules["graphiti_core.models"] = MagicMock()
sys.modules["graphiti_core.models.nodes"] = MagicMock()
sys.modules["graphiti_core.models.nodes.node_db_queries"] = MagicMock()

# Mocking the specific function we want to patch
# We need a real function object for wraps/inspect to work, not just MagicMock
def dummy_bulk_import(node_type, properties, id_property):
    return "MERGE (n) SET n:$(node.labels)"

# We need to structure mocks so that imports inside apply_patches work
# Reset mocks for clean state
sys.modules.pop("graphiti_core", None)
sys.modules.pop("graphiti_core.utils", None)
sys.modules.pop("graphiti_core.utils.bulk_utils", None)

mock_core = MagicMock()
mock_utils = MagicMock()
mock_bulk = MagicMock()

# Attach our real dummy function to the mock module
mock_bulk.bulk_import_statement_for_node = dummy_bulk_import

# Link modules together so 'from graphiti_core.utils import bulk_utils' works
mock_utils.bulk_utils = mock_bulk

# Re-register in sys.modules
sys.modules["graphiti_core"] = mock_core
sys.modules["graphiti_core.utils"] = mock_utils
sys.modules["graphiti_core.utils.bulk_utils"] = mock_bulk

# Mock models nodes as well
mock_models = MagicMock()
mock_nodes = MagicMock()
mock_queries = MagicMock()
sys.modules["graphiti_core.models"] = mock_models
sys.modules["graphiti_core.models.nodes"] = mock_nodes
sys.modules["graphiti_core.models.nodes.node_db_queries"] = mock_queries

# Now we can import our scripts
from scripts.apply_patches import apply_patches
from knowledge.ingest import remember_text, find_similar_episode
from scripts.consolidate import consolidate_l3_memory
from core.memory_ops import MemoryOps

# --- Test 1: Verify Patching ---
def test_apply_patches_fixes_string():
    # Run patch
    apply_patches()
    
    # Execute the function that sits on the mock module
    # Note: apply_patches modifies the attribute on the module object
    result = sys.modules["graphiti_core.utils.bulk_utils"].bulk_import_statement_for_node("Entity", {}, "uuid")
    
    # Verify replacement
    assert "SET n:Entity" in result
    assert "SET n:$(node.labels)" not in result
    print("\n✅ Test 1: Patching works")

# --- Test 2: Semantic Deduplication Logic ---
@pytest.mark.asyncio
async def test_semantic_deduplication():
    # Mocks
    mock_graphiti = MagicMock()
    mock_driver = AsyncMock()
    mock_graphiti.driver = mock_driver
    
    # Scenario: Vector search finds a match
    mock_driver.execute_query.return_value.records = [{"uuid": "existing-uuid", "score": 0.98}]
    
    # Mock embeddings to return a vector
    with patch("knowledge.ingest.get_embedding", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [0.1, 0.2, 0.3]
        
        # Mock settings to return a group id
        with patch("knowledge.ingest._get_group_id", return_value="personal"):
            
            # Execute
            result = await remember_text(mock_graphiti, "I love pizza", memory_type="personal")
            
            # Verify
            assert result["status"] == "ok"
            assert result["message"] == "merged with existing memory"
            assert result["uuid"] == "existing-uuid"
            
            # Check that we ran the UPDATE query, not ADD EPISODE
            # The exact query text check is fragile, checking keywords
            calls = mock_driver.execute_query.call_args_list
            update_call = [c for c in calls if "SET e.last_seen_at" in c[0][0]]
            assert len(update_call) > 0
            
            # Ensure we didn't call add_episode (which is on mock_graphiti object)
            mock_graphiti.add_episode.assert_not_called()
            
    print("\n✅ Test 2: Semantic Deduplication logic works")

# --- Test 3: L3 Consolidation Logic ---
@pytest.mark.asyncio
async def test_l3_consolidation():
    # Mocks
    mock_graphiti = MagicMock()
    mock_driver = AsyncMock()
    mock_graphiti.driver = mock_driver
    
    # Mock finding episodes: 5 episodes for "project" group
    episodes_data = [
        {"uuid": f"uuid-{i}", "text": f"fact {i}", "group_id": "project", "reference_time": "2023-01-01"} 
        for i in range(5)
    ]
    
    # First query returns episodes
    mock_driver.execute_query.side_effect = [
        MagicMock(records=episodes_data), # Search result
        None, # Create L3 result
        None  # Link result
    ]
    
    # Mock LLM
    with patch("scripts.consolidate.llm_summarize", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = "High level summary of project"
        
        await consolidate_l3_memory(mock_graphiti)
        
        # Verify LLM was called with list of texts
        mock_llm.assert_called_once()
        args = mock_llm.call_args
        assert len(args[0][0]) == 5 # 5 texts passed
        assert "project" in args[1]['context']
        
        # Verify Cypher CREATE L3Summary was called
        create_calls = [c for c in mock_driver.execute_query.call_args_list if "CREATE (s:L3Summary" in c[0][0]]
        assert len(create_calls) == 1
        assert "High level summary" in create_calls[0][1]['summary_text']

    print("\n✅ Test 3: L3 Consolidation flow works")

# Test 4 removed: test_adaptive_search_priority() was testing SimpleAgent's adaptive search.
# Functionality is covered by other tests (test_memory_ops, test_search_memory, test_integration).

if __name__ == "__main__":
    # Manually run async tests if executed as script
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    test_apply_patches_fixes_string()
    loop.run_until_complete(test_semantic_deduplication())
    loop.run_until_complete(test_l3_consolidation())
    # test_adaptive_search_priority() removed (SimpleAgent deleted)
    loop.close()
