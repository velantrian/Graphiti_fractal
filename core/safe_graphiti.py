import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Read/filter outcome statuses (aligned with core.context_guard vocabulary).
FILTER_OK = "OK"
FILTER_EMPTY = "EMPTY"
FILTER_DEGRADED_ERROR = "DEGRADED_ERROR"


class _ValidationError(Exception):
    """Internal marker: validation raised, distinct from 'invalid/missing fields'."""


def safe_entity(item: Any) -> Optional[Dict[str, Any]]:
    """Backward-compatible wrapper: returns None for invalid OR errored items.

    Use ``_entity_checked`` when the caller must distinguish the two.
    """
    try:
        return _entity_checked(item)
    except _ValidationError:
        return None


def _entity_checked(item: Any) -> Optional[Dict[str, Any]]:
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
        raise _ValidationError(str(e)) from e

def safe_edge(item: Any) -> Optional[Dict[str, Any]]:
    """Backward-compatible wrapper; see ``_edge_checked``."""
    try:
        return _edge_checked(item)
    except _ValidationError:
        return None


def _edge_checked(item: Any) -> Optional[Dict[str, Any]]:
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
        raise _ValidationError(str(e)) from e

def filter_graphiti_results(results: Any) -> Dict[str, List]:
    """
    Filters entities and edges from Graphiti AddEpisodeResults.
    """
    entities = []
    edges = []
    
    raw_entities = getattr(results, "extracted_entities", []) or []
    raw_edges = getattr(results, "extracted_edges", []) or []
    
    errored_entities = 0
    errored_edges = 0
    for ent in raw_entities:
        try:
            s_ent = _entity_checked(ent)
        except _ValidationError:
            errored_entities += 1
            continue
        if s_ent:
            entities.append(s_ent)

    for edge in raw_edges:
        try:
            s_edge = _edge_checked(edge)
        except _ValidationError:
            errored_edges += 1
            continue
        if s_edge:
            edges.append(s_edge)

    dropped_entities = len(raw_entities) - len(entities)
    dropped_edges = len(raw_edges) - len(edges)
    if errored_entities or errored_edges:
        status = FILTER_DEGRADED_ERROR
    elif entities or edges:
        status = FILTER_OK
    else:
        status = FILTER_EMPTY
    
    if dropped_entities > 0 or dropped_edges > 0:
        logger.warning(f"Dropped malformed graph elements: entities={dropped_entities}, edges={dropped_edges}")
        
    return {
        "entities": entities,
        "edges": edges,
        "dropped_entities": dropped_entities,
        "dropped_edges": dropped_edges,
        "errored_entities": errored_entities,
        "errored_edges": errored_edges,
        "status": status,
    }
