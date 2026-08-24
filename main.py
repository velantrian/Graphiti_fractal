import argparse
import asyncio
from datetime import datetime, timezone

from benchmarks.benchmark import run_benchmark
from core import get_graphiti_client
from core.graphiti_client import get_write_semaphore
from core.migrations import apply_migrations
from layers.l1_consolidation import get_l1_context
from layers.l2_semantic import get_l2_semantic_context
from layers.l3_fractal import get_l3_fractal_context
from queries.context_builder import build_agent_context
from queries.dedupe_entities import main as dedupe_entities_main
from queries.quality_check import check_graph_quality
from queries.search_strategies import test_search_strategies
from scripts.consolidate import consolidate_l3_memory
from visualization.visualization_export import export_to_file


async def ensure_graphiti():
    return await get_graphiti_client().ensure_ready()


async def cmd_setup(args):
    graphiti = await ensure_graphiti()
    migration = await apply_migrations(graphiti)
    print("✅ Graphiti/Neo4j готов, индексы созданы")
    if migration["total"] > 0:
        print(
            f"✅ Migrations: applied={migration['applied']} "
            f"skipped={migration['skipped']} total={migration['total']}"
        )


async def cmd_seed(args):
    graphiti = await ensure_graphiti()
    episodes = [
        dict(
            name="Project Overview",
            body="""
            Sergey and Natasha are working on a Fractal Memory project.

            The project has three main components:
            1. Graph Engine - built with Neo4j for knowledge representation
            2. LLM Integration - using GPT-4 for entity extraction and reasoning
            3. Temporal Processing - maintaining bi-temporal data (valid_from, valid_to)

            The project status is in Development phase.
            Sergey is the primary developer.
            Priority is High - this is a core research initiative.
            """,
            source="project_documentation",
        ),
        dict(
            name="Strategic Decision - Vanilla First",
            body="""
            Decision made on 2025-12-10:
            We decided to simplify the Fractal Memory implementation by starting with
            vanilla Graphiti instead of building custom Redis buffer layer.

            Rationale: Reduce complexity and focus on core value.
            Status: Active.
            """,
            source="decision_log",
        ),
        dict(
            name="Team Structure",
            body="""
            The development team consists of Sergey and Natasha.
            Focus: Building a memory system for AI agents.
            """,
            source="team_documentation",
        ),
    ]

    write_semaphore = get_write_semaphore()
    for episode in episodes:
        async with write_semaphore:
            await graphiti.add_episode(
                name=episode["name"],
                episode_body=episode["body"],
                source_description=episode["source"],
                reference_time=datetime.now(timezone.utc),
            )
        print(f"✅ Added episode: {episode['name']}")

    from knowledge.ingest import link_user_to_person_entity

    await link_user_to_person_entity(graphiti, "sergey", "Sergey")
    if args.with_search:
        await cmd_search_demo(args)
    print("✨ Demo data loaded")


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
    context = await build_agent_context(graphiti, entity_name=args.entity, context_size=args.size)
    print(context if context else "⚠️  Сущность не найдена")


async def cmd_l1(args):
    graphiti = await ensure_graphiti()
    print(await get_l1_context(graphiti, user_context=args.query, hours_back=args.hours))


async def cmd_l2(args):
    graphiti = await ensure_graphiti()
    summary = await get_l2_semantic_context(graphiti, args.entity)
    print(summary if summary else "⚠️  Сущность не найдена")


async def cmd_l3(args):
    graphiti = await ensure_graphiti()
    summary = await get_l3_fractal_context(graphiti, args.entity)
    print(summary if summary else "⚠️  Сущность не найдена")


async def cmd_viz_export(args):
    graphiti = await ensure_graphiti()
    await export_to_file(graphiti, filename=args.output)


async def cmd_benchmark(args):
    await run_benchmark()


async def cmd_consolidate(args):
    graphiti = await ensure_graphiti()
    await consolidate_l3_memory(graphiti, hours_back=args.hours)


async def cmd_dedupe_entities(args):
    await dedupe_entities_main(dry_run=not args.apply)


def build_parser():
    parser = argparse.ArgumentParser(description="Fractal Memory / Graphiti CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("setup", help="Создать индексы/констрейнты Graphiti").set_defaults(func=cmd_setup)

    seed_parser = subparsers.add_parser("seed", help="Создать демо-эпизоды и сущности")
    seed_parser.add_argument("--with-search", action="store_true")
    seed_parser.set_defaults(func=cmd_seed)

    subparsers.add_parser("quality", help="Отчёт по качеству графа").set_defaults(func=cmd_quality)

    clear_parser = subparsers.add_parser("clear", help="Полностью очистить граф")
    clear_parser.add_argument(
        "--confirm",
        required=True,
        help="Для destructive clear требуется точное значение CLEAR_ALL_MEMORY",
    )
    clear_parser.set_defaults(func=cmd_clear)

    subparsers.add_parser("migrate", help="Применить миграции").set_defaults(func=cmd_migrate)
    subparsers.add_parser("search-demo", help="Демонстрация поиска").set_defaults(func=cmd_search_demo)

    context_parser = subparsers.add_parser("context", help="Построить контекст для сущности")
    context_parser.add_argument("entity")
    context_parser.add_argument("--size", choices=["minimal", "medium", "full"], default="full")
    context_parser.set_defaults(func=cmd_context)

    l1_parser = subparsers.add_parser("l1", help="L1 recent context")
    l1_parser.add_argument("--query", default="Fractal Memory")
    l1_parser.add_argument("--hours", type=int, default=24)
    l1_parser.set_defaults(func=cmd_l1)

    l2_parser = subparsers.add_parser("l2", help="L2 semantic communities")
    l2_parser.add_argument("entity")
    l2_parser.set_defaults(func=cmd_l2)

    l3_parser = subparsers.add_parser("l3", help="L3 fractal abstraction")
    l3_parser.add_argument("entity")
    l3_parser.set_defaults(func=cmd_l3)

    viz_parser = subparsers.add_parser("viz-export", help="Экспорт графа для static viewer")
    viz_parser.add_argument(
        "--output",
        default="static/graph_data.json",
        help="Путь к JSON (по умолчанию static/graph_data.json)",
    )
    viz_parser.set_defaults(func=cmd_viz_export)

    subparsers.add_parser("benchmark", help="Performance benchmark").set_defaults(func=cmd_benchmark)

    consolidate_parser = subparsers.add_parser("consolidate", help="Запустить L3 consolidation")
    consolidate_parser.add_argument("--hours", type=int, default=24 * 7)
    consolidate_parser.set_defaults(func=cmd_consolidate)

    dedupe_parser = subparsers.add_parser("dedupe-entities", help="Namespace-safe Entity dedupe")
    dedupe_parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить изменения. Без флага выполняется только dry-run.",
    )
    dedupe_parser.set_defaults(func=cmd_dedupe_entities)

    return parser


def main():
    args = build_parser().parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
