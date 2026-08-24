"""CLI for side-effect-free graph analytics planning.

This does not require Neo4j GDS and never executes mutate/write modes.
"""

from __future__ import annotations

import argparse
import json

from core.derived_graph import plan_gds_analysis


ALGORITHMS = {
    "louvain": "community detection",
    "leiden": "community detection",
    "pagerank": "centrality",
    "betweenness": "centrality",
    "shortest-path": "pathfinding",
    "link-prediction": "prediction/research",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview bounded graph analytics")
    parser.add_argument("algorithm", choices=sorted(ALGORITHMS))
    parser.add_argument("--mode", choices=["stream", "stats"], default="stream")
    parser.add_argument("--projection", default="ephemeral")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    plan = plan_gds_analysis(
        algorithm=args.algorithm,
        mode=args.mode,
        projection=args.projection,
    )
    plan["role"] = ALGORITHMS[args.algorithm]
    plan["execution"] = "NOT_RUN"
    plan["predicted_links_are_facts"] = False
    print(json.dumps(plan, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
