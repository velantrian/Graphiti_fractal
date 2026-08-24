import argparse
import asyncio
import json
from pathlib import Path

from benchmarks.benchmark import run_benchmark
from core import get_graphiti_client
from core.instance import get_instance_user_id
from core.memory_doctor import collect_memory_status
from core.memory_import import apply_import_plan, build_import_plan
from core.memory_lifecycle import PromotionSignals, explain_promotion, plan_consolidation
from core.memory_ops import MemoryOps
from core.migrations import apply_migrations
from layers.l1_consolidation import get_l1_context
from layers.l2_semantic import get_l2_semantic_context
from layers.l3_fractal import build_l3_profile, get_l3_fractal_context
from queries.context_builder import build_agent_context
from queries.dedupe import main as dedupe_episodes_main
from queries.dedupe_entities import main as dedupe_entities_main
from queries.quality_check import check_graph_quality
from queries.search_strategies import test_search_strategies
from visualization.visualization_export import export_to_file


async def ensure_graphiti():
    return await get_graphiti_client().ensure_ready()


async def cmd_setup(args):
    graphiti = await ensure_graphiti()
    migration = await apply_migrations(graphiti)
    print("✅ Graphiti/Neo4j готов, индексы созданы")
    if migration["total"]:
        print(
            f"✅ Migrations: applied={migration['applied']} "
            f"skipped={migration['skipped']} total={migration['total']}"
        )


async def cmd_seed(args):
    """Load small demo data through the same canonical ingest path as the app."""
    graphiti = await ensure_graphiti()
    owner = get_instance_user_id()
    memory = MemoryOps(graphiti, owner)
    demo = [
        (
            "project",
            "project_documentation",
            "Fractal Memory uses Neo4j and Graphiti for a local memory service. "
            "The active architecture separates API/MCP/CLI surfaces from the memory service.",
        ),
        (
            "project",
            "decision_log",
            "Decision: prefer Graphiti-native primitives over custom graph mutations and legacy compatibility patches.",
        ),
        (
            "knowledge",
            "team_documentation",
            "Fractal Memory is designed as a local single-owner service with explicit namespace-scoped retrieval.",
        ),
    ]
    for memory_type, source, text in demo:
        result = await memory.ingest_pipeline(
            text,
            source_description=source,
            memory_type=memory_type,
        )
        print(f"✅ {source}: {result['status']} added={result.get('added', 0)} skipped={result.get('skipped', 0)}")

    if args.with_search:
        await cmd_search_demo(args)


async def cmd_clear(args):
    if args.confirm != "CLEAR_ALL_MEMORY":
        raise SystemExit("Refusing destructive clear: pass --confirm CLEAR_ALL_MEMORY")
    graphiti = await ensure_graphiti()
    await graphiti.driver.execute_query("MATCH (n) DETACH DELETE n")
    await graphiti.build_indices_and_constraints()
    await apply_migrations(graphiti)
    print("🧹 Graph cleared and indices recreated")


async def cmd_migrate(args):
    graphiti = await ensure_graphiti()
    migration = await apply_migrations(graphiti)
    print(
        f"✅ Migrations: applied={migration['applied']} "
        f"skipped={migration['skipped']} total={migration['total']}"
    )


async def cmd_quality(args):
    await check_graph_quality()


async def cmd_search_demo(args):
    await test_search_strategies()


async def cmd_context(args):
    graphiti = await ensure_graphiti()
    context = await build_agent_context(
        graphiti,
        entity_name=args.entity,
        context_size=args.size,
    )
    print(context if context else "⚠️ Сущность не найдена")


async def cmd_l1(args):
    graphiti = await ensure_graphiti()
    print(await get_l1_context(graphiti, user_context=args.query, hours_back=args.hours))


async def cmd_l2(args):
    graphiti = await ensure_graphiti()
    summary = await get_l2_semantic_context(graphiti, args.entity)
    print(summary if summary else "⚠️ Сущность не найдена")


async def cmd_l3(args):
    graphiti = await ensure_graphiti()
    print(await get_l3_fractal_context(graphiti, args.entity))


async def cmd_l3_build(args):
    graphiti = await ensure_graphiti()
    profile = await build_l3_profile(
        graphiti,
        args.entity,
        user_id=get_instance_user_id(),
    )
    print(profile if profile else "⚠️ Недостаточно L2 context для L3 profile")


async def cmd_viz_export(args):
    await export_to_file(await ensure_graphiti(), filename=args.output)


async def cmd_benchmark(args):
    await run_benchmark()


async def cmd_dedupe_entities(args):
    await dedupe_entities_main(dry_run=not args.apply)


async def cmd_dedupe_episodes(args):
    await dedupe_episodes_main(
        dry_run=not args.apply,
        purge_days=args.purge_deleted_days if args.apply else None,
    )


async def cmd_memory_status(args):
    status = await collect_memory_status(await ensure_graphiti(), deep=args.deep)
    print(json.dumps(status, ensure_ascii=False, indent=2))


async def cmd_memory_import(args):
    plan = build_import_plan(args.path, source_type=args.source_type)
    if not args.apply:
        public = {key: value for key, value in plan.items() if key != "_payload"}
        print(json.dumps(public, ensure_ascii=False, indent=2))
        return
    graphiti = await ensure_graphiti()
    memory = MemoryOps(graphiti, get_instance_user_id())
    result = await apply_import_plan(memory, plan, apply=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))


async def cmd_memory_promote_explain(args):
    explanation = explain_promotion(
        PromotionSignals(
            relevance=args.relevance,
            frequency=args.frequency,
            query_diversity=args.query_diversity,
            recency=args.recency,
            consolidation=args.consolidation,
            conceptual_richness=args.conceptual_richness,
        ),
        origin_class=args.origin_class,
        recall_count=args.recall_count,
        unique_queries=args.unique_queries,
    )
    print(json.dumps(explanation, ensure_ascii=False, indent=2))


async def cmd_memory_consolidate_preview(args):
    path = Path(args.path).expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates = payload if isinstance(payload, list) else payload.get("candidates", [])
    print(json.dumps(plan_consolidation(candidates), ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="Fractal Memory / Graphiti CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("setup", help="Создать индексы/constraints").set_defaults(func=cmd_setup)

    seed = subparsers.add_parser("seed", help="Загрузить bounded demo data")
    seed.add_argument("--with-search", action="store_true")
    seed.set_defaults(func=cmd_seed)

    subparsers.add_parser("quality", help="Отчёт по качеству графа").set_defaults(func=cmd_quality)

    clear = subparsers.add_parser("clear", help="Полностью очистить граф")
    clear.add_argument("--confirm", required=True, help="Точное значение CLEAR_ALL_MEMORY")
    clear.set_defaults(func=cmd_clear)

    subparsers.add_parser("migrate", help="Применить миграции").set_defaults(func=cmd_migrate)
    subparsers.add_parser("search-demo", help="Демонстрация поиска").set_defaults(func=cmd_search_demo)

    context = subparsers.add_parser("context", help="Построить контекст для сущности")
    context.add_argument("entity")
    context.add_argument("--size", choices=["minimal", "medium", "full"], default="full")
    context.set_defaults(func=cmd_context)

    l1 = subparsers.add_parser("l1", help="L1 recent context")
    l1.add_argument("--query", default="Fractal Memory")
    l1.add_argument("--hours", type=int, default=24)
    l1.set_defaults(func=cmd_l1)

    l2 = subparsers.add_parser("l2", help="L2 semantic communities")
    l2.add_argument("entity")
    l2.set_defaults(func=cmd_l2)

    l3 = subparsers.add_parser("l3", help="Прочитать latest L3 profile")
    l3.add_argument("entity")
    l3.set_defaults(func=cmd_l3)

    l3_build = subparsers.add_parser("l3-build", help="Синтезировать bounded L3 profile")
    l3_build.add_argument("entity")
    l3_build.set_defaults(func=cmd_l3_build)

    viz = subparsers.add_parser("viz-export", help="Экспорт графа для static viewer")
    viz.add_argument("--output", default="static/graph_data.json")
    viz.set_defaults(func=cmd_viz_export)

    subparsers.add_parser("benchmark", help="Performance benchmark").set_defaults(func=cmd_benchmark)

    dedupe_entities = subparsers.add_parser("dedupe-entities", help="Namespace-safe Entity dedupe")
    dedupe_entities.add_argument("--apply", action="store_true", help="Без флага только dry-run")
    dedupe_entities.set_defaults(func=cmd_dedupe_entities)

    dedupe_episodes = subparsers.add_parser("dedupe-episodes", help="Namespace-safe Episodic dedupe")
    dedupe_episodes.add_argument("--apply", action="store_true", help="Без флага только dry-run")
    dedupe_episodes.add_argument(
        "--purge-deleted-days",
        type=int,
        default=None,
        help="Только вместе с --apply: hard-delete soft-deleted старше N дней",
    )
    dedupe_episodes.set_defaults(func=cmd_dedupe_episodes)

    memory_status = subparsers.add_parser("memory-status", help="Read-only memory diagnostics")
    memory_status.add_argument(
        "--deep",
        action="store_true",
        help="Probe provider-backed semantic retrieval; may make external provider calls",
    )
    memory_status.set_defaults(func=cmd_memory_status)

    memory_import = subparsers.add_parser("memory-import", help="Preview/apply isolated external memory import")
    memory_import.add_argument("path")
    memory_import.add_argument("--source-type", default="auto")
    memory_import.add_argument("--apply", action="store_true", help="Without this flag no writes occur")
    memory_import.set_defaults(func=cmd_memory_import)

    promote = subparsers.add_parser("memory-promote-explain", help="Explain deterministic promotion gates")
    promote.add_argument("--origin-class", choices=["owner", "agent_derived", "untrusted", "system"], default="owner")
    promote.add_argument("--recall-count", type=int, default=0)
    promote.add_argument("--unique-queries", type=int, default=0)
    promote.add_argument("--relevance", type=float, default=0.0)
    promote.add_argument("--frequency", type=float, default=0.0)
    promote.add_argument("--query-diversity", type=float, default=0.0)
    promote.add_argument("--recency", type=float, default=0.0)
    promote.add_argument("--consolidation", type=float, default=0.0)
    promote.add_argument("--conceptual-richness", type=float, default=0.0)
    promote.set_defaults(func=cmd_memory_promote_explain)

    consolidate = subparsers.add_parser(
        "memory-consolidate-preview",
        help="Plan collect -> patterns -> promotion from a JSON candidate file; never writes",
    )
    consolidate.add_argument("path")
    consolidate.set_defaults(func=cmd_memory_consolidate_preview)

    return parser


def main():
    args = build_parser().parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
