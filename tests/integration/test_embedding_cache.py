"""
Integration tests for embedding cache.

Tests LRU and TTL behavior of the embedding cache.
"""

import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
import tempfile

# Local cache only: no Neo4j admission needed.
pytestmark = pytest.mark.no_neo4j


@pytest.mark.asyncio
async def test_cache_lru_eviction():
    """Test that LRU eviction works correctly."""
    from core.embeddings import EmbeddingCache
    
    # Create small cache
    cache = EmbeddingCache(max_size=3, ttl_hours=24)
    
    # Add 3 items
    await cache.set("key1", [1.0, 2.0, 3.0])
    await cache.set("key2", [4.0, 5.0, 6.0])
    await cache.set("key3", [7.0, 8.0, 9.0])
    
    # Access key1 to make it recently used
    await cache.get("key1")
    
    # Add 4th item - should evict key2 (least recently used)
    await cache.set("key4", [10.0, 11.0, 12.0])
    
    # key1 should still exist (was accessed)
    assert await cache.get("key1") is not None
    
    # key2 should be evicted
    assert await cache.get("key2") is None
    
    # key3 and key4 should exist
    assert await cache.get("key3") is not None
    assert await cache.get("key4") is not None


@pytest.mark.asyncio
async def test_cache_ttl_expiration():
    """Test that TTL expiration works correctly."""
    from core.embeddings import EmbeddingCache, CacheEntry
    
    # Create cache with 1 hour TTL
    cache = EmbeddingCache(max_size=100, ttl_hours=1)
    
    # Manually add an expired entry
    old_time = datetime.now(timezone.utc) - timedelta(hours=2)
    cache._cache["expired_key"] = CacheEntry(
        embedding=[1.0, 2.0],
        created_at=old_time
    )
    cache._loaded = True
    
    # Should return None for expired entry
    result = await cache.get("expired_key")
    assert result is None


@pytest.mark.asyncio
async def test_cache_persistence():
    """Test that cache persists to disk."""
    from core.embeddings import EmbeddingCache
    
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_file = Path(tmpdir) / "test_cache.json"
        
        # Create cache and add data
        cache1 = EmbeddingCache(max_size=100, ttl_hours=24, cache_file=cache_file)
        await cache1.set("persist_key", [1.0, 2.0, 3.0])
        await cache1.save()
        
        # Create new cache instance and load
        cache2 = EmbeddingCache(max_size=100, ttl_hours=24, cache_file=cache_file)
        result = await cache2.get("persist_key")
        
        assert result is not None
        assert result == [1.0, 2.0, 3.0]


@pytest.mark.asyncio
async def test_cache_stats():
    """Test cache statistics."""
    from core.embeddings import EmbeddingCache
    import tempfile
    from pathlib import Path
    
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_file = Path(tmpdir) / "test_stats_cache.json"
        cache = EmbeddingCache(max_size=100, ttl_hours=24, cache_file=cache_file)
        
        # Add and access items
        await cache.set("key1", [1.0])
        await cache.get("key1")  # Hit
        await cache.get("key2")  # Miss
        
        stats = cache.stats()
        
        assert stats["size"] == 1
        assert stats["max_size"] == 100
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert "hit_rate" in stats


@pytest.mark.asyncio
async def test_cache_cleanup_expired():
    """Test cleanup of expired entries."""
    from core.embeddings import EmbeddingCache, CacheEntry
    
    cache = EmbeddingCache(max_size=100, ttl_hours=1)
    cache._loaded = True
    
    # Add mix of fresh and expired entries
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(hours=2)
    
    cache._cache["fresh"] = CacheEntry(embedding=[1.0], created_at=now)
    cache._cache["expired1"] = CacheEntry(embedding=[2.0], created_at=old_time)
    cache._cache["expired2"] = CacheEntry(embedding=[3.0], created_at=old_time)
    
    # Cleanup
    removed = await cache.cleanup_expired()
    
    assert removed == 2
    assert "fresh" in cache._cache
    assert "expired1" not in cache._cache
    assert "expired2" not in cache._cache


@pytest.mark.asyncio
async def test_cache_clear():
    """Test cache clearing."""
    from core.embeddings import EmbeddingCache
    
    with tempfile.TemporaryDirectory() as tmpdir:
        cache_file = Path(tmpdir) / "test_cache.json"
        cache = EmbeddingCache(max_size=100, ttl_hours=24, cache_file=cache_file)
        
        await cache.set("key1", [1.0])
        await cache.set("key2", [2.0])
        
        assert cache.stats()["size"] == 2
        
        await cache.clear()
        
        assert cache.stats()["size"] == 0
        assert await cache.get("key1") is None
