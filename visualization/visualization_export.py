import asyncio
import json
import logging
from pathlib import Path

from core import get_graphiti_client

logger = logging.getLogger(__name__)

PUBLIC_STATIC_DIR = (Path(__file__).resolve().parent.parent / "static").resolve()
CANONICAL_PUBLIC_GRAPH_DATA = (PUBLIC_STATIC_DIR / "graph_data.json").resolve()


def validate_export_path(filename: str) -> Path:
    """Reject alternate graph-data filenames inside the public static tree.

    The canonical ``static/graph_data.json`` path is safe because the application
    explicitly blocks it on the public static mount and serves it only through
    the authenticated visualization route. Exports outside ``static`` remain
    available for operator tooling and are not exposed by the web application.
    """
    path = Path(filename).expanduser().resolve()
    try:
        path.relative_to(PUBLIC_STATIC_DIR)
    except ValueError:
        return path

    if path != CANONICAL_PUBLIC_GRAPH_DATA:
        raise ValueError(
            "visualization export inside public static is restricted to "
            "static/graph_data.json"
        )
    return path


async def export_graph_for_vis(graphiti, limit: int = 500):
    """Export the bounded active Entity/Community graph for the authenticated D3 view."""
    driver = getattr(graphiti, "driver", None) or getattr(graphiti, "_driver", None)
    if not driver:
        logger.error("Graphiti driver not found for export")
        return {"nodes": [], "edges": [], "error": "No driver"}

    nodes_map = {}
    edges_list = []

    query_nodes = """
    MATCH (n)
    WHERE (n:Entity OR n:Community)
      AND n.uuid IS NOT NULL
      AND coalesce(n.deleted, false) = false
    RETURN n.uuid as uuid, n.name as name, labels(n) as labels, n.group_id as group_id, n.summary as summary
    LIMIT $limit
    """

    try:
        if hasattr(driver, "execute_query"):
            res_nodes = await driver.execute_query(query_nodes, limit=limit)
            records_nodes = res_nodes.records
        else:
            async with driver.session() as session:
                res_nodes = await session.run(query_nodes, limit=limit)
                records_nodes = await res_nodes.list()

        for rec in records_nodes:
            uuid = rec["uuid"]
            labels = rec["labels"]
            node_type = "Community" if "Community" in labels else "Entity"

            nodes_map[uuid] = {
                "id": str(uuid),
                "label": rec["name"] or f"{node_type}:{str(uuid)[:4]}",
                "title": rec["summary"] or "",
                "type": node_type,
                "group": rec["group_id"] or "default",
                "size": 30 if node_type == "Community" else 20,
            }

    except Exception as e:
        logger.error(f"Error exporting nodes: {e}")

    query_edges = """
    MATCH (n)-[r]->(m)
    WHERE (n:Entity OR n:Community)
      AND (m:Entity OR m:Community)
      AND n.uuid IS NOT NULL
      AND m.uuid IS NOT NULL
      AND coalesce(n.deleted, false) = false
      AND coalesce(m.deleted, false) = false
    RETURN n.uuid as source, m.uuid as target, type(r) as type, r.fact as fact
    LIMIT $limit
    """

    try:
        if hasattr(driver, "execute_query"):
            res_edges = await driver.execute_query(query_edges, limit=limit * 2)
            records_edges = res_edges.records
        else:
            async with driver.session() as session:
                res_edges = await session.run(query_edges, limit=limit * 2)
                records_edges = await res_edges.list()

        for rec in records_edges:
            src = rec["source"]
            tgt = rec["target"]
            if src in nodes_map and tgt in nodes_map:
                # D3 forceLink consumes `source` / `target` by default. Keep the
                # producer schema aligned with the browser consumer instead of
                # relying on a frontend adapter that can silently drift.
                edges_list.append(
                    {
                        "source": str(src),
                        "target": str(tgt),
                        "label": rec["type"],
                        "title": rec["fact"] or "",
                        "arrows": "to",
                    }
                )

    except Exception as e:
        logger.error(f"Error exporting edges: {e}")

    nodes_data = list(nodes_map.values())

    return {
        "nodes": nodes_data,
        "edges": edges_list,
        "statistics": {
            "total_nodes": len(nodes_data),
            "total_edges": len(edges_list),
            "node_types": list(set(n["type"] for n in nodes_data)),
        },
    }


async def export_to_file(graphiti, filename: str = "visualization/graph_data.json"):
    output_path = validate_export_path(filename)
    data = await export_graph_for_vis(graphiti)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"✅ Exported to {output_path}")
    print(f"   Nodes: {data['statistics']['total_nodes']}")
    print(f"   Edges: {data['statistics']['total_edges']}")


async def test_export():
    graphiti_client = get_graphiti_client()
    graphiti = await graphiti_client.ensure_ready()
    await export_to_file(graphiti)


if __name__ == "__main__":
    asyncio.run(test_export())
