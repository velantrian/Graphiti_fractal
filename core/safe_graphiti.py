import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

def safe_entity(item: Any) -> Optional[Dict[str, Any]]:
    """
    Safely extracts and validates an entity from Graphiti result.
    Returns a dict if valid, None otherwise.
    """
    if item is None:
        return None
    
    try:
        # If it's already a dict
        if isinstance(item, dict):
            uuid = item.get("uuid")
            name = item.get("name")
            if not uuid or not name:
                logger.warning(f"Skipping entity with missing fields: uuid={uuid}, name={name}")
                return None
            return item
        
        # If it's an object (likely a Pydantic model from graphiti_core)
        uuid = getattr(item, "uuid", None)
        name = getattr(item, "name", None)
        
        if not uuid or not name:
            logger.warning(f"Skipping entity object with missing fields: uuid={uuid}, name={name}")
            return None
            
        # Return as dict for safety
        return {
            "uuid": uuid,
            "name": name,
            "summary": getattr(item, "summary", ""),
            "node_type": getattr(item, "node_type", "Entity")
        }
    except Exception as e:
        logger.error(f"Error validating entity: {e}")
        return None

def safe_edge(item: Any) -> Optional[Dict[str, Any]]:
    """
    Safely extracts and validates an edge from Graphiti result.
    """
    if item is None:
        return None
        
    try:
        source = getattr(item, "source_node_uuid", None)
        target = getattr(item, "target_node_uuid", None)
        rel_type = getattr(item, "relationship_type", None)
        
        if not source or not target or not rel_type:
            logger.warning(f"Skipping edge with missing fields: source={source}, target={target}, type={rel_type}")
            return None
            
        return {
            "source_node_uuid": source,
            "target_node_uuid": target,
            "relationship_type": rel_type,
            "fact": getattr(item, "fact", "")
        }
    except Exception as e:
        logger.error(f"Error validating edge: {e}")
        return None

def filter_graphiti_results(results: Any) -> Dict[str, List]:
    """
    Filters entities and edges from Graphiti AddEpisodeResults.
    """
    entities = []
    edges = []
    
    raw_entities = getattr(results, "extracted_entities", []) or []
    raw_edges = getattr(results, "extracted_edges", []) or []
    
    for ent in raw_entities:
        s_ent = safe_entity(ent)
        if s_ent:
            entities.append(s_ent)
            
    for edge in raw_edges:
        s_edge = safe_edge(edge)
        if s_edge:
            edges.append(s_edge)
            
    dropped_entities = len(raw_entities) - len(entities)
    dropped_edges = len(raw_edges) - len(edges)
    
    if dropped_entities > 0 or dropped_edges > 0:
        logger.warning(f"Dropped malformed graph elements: entities={dropped_entities}, edges={dropped_edges}")
        
    return {
        "entities": entities,
        "edges": edges,
        "dropped_entities": dropped_entities,
        "dropped_edges": dropped_edges
    }
