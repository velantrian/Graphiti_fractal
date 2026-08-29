from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Iterable


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


@dataclass(frozen=True)
class Migration:
    migration_id: str
    checksum: str
    statements: list[str]


def _split_cypher(text: str) -> list[str]:
    # Простое разделение по ';'. Для наших миграций достаточно.
    parts = []
    buf = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip()
            stmt = stmt[:-1].strip()  # убрать ';'
            if stmt:
                parts.append(stmt)
            buf = []
    # хвост без ';'
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def load_migrations() -> list[Migration]:
    if not MIGRATIONS_DIR.exists():
        return []
    migrations: list[Migration] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.cypher")):
        raw = path.read_text(encoding="utf-8")
        checksum = sha256(raw.encode("utf-8")).hexdigest()
        statements = _split_cypher(raw)
        migrations.append(Migration(migration_id=path.name, checksum=checksum, statements=statements))
    return migrations


async def ensure_migrations_table(graphiti) -> None:
    driver = graphiti.driver
    await driver.execute_query(
        """
        CREATE CONSTRAINT migration_id_unique IF NOT EXISTS
        FOR (m:Migration)
        REQUIRE m.migration_id IS UNIQUE
        """
    )


async def applied_migrations(graphiti) -> dict[str, str | None]:
    """Return the durable migration ledger as migration_id -> checksum."""
    driver = graphiti.driver
    res = await driver.execute_query(
        "MATCH (m:Migration) RETURN m.migration_id AS id, m.checksum AS checksum"
    )
    return {rec["id"]: rec["checksum"] for rec in res.records}


async def applied_migration_ids(graphiti) -> set[str]:
    """Backward-compatible ID-only view of the durable migration ledger."""
    return set((await applied_migrations(graphiti)).keys())


async def apply_migrations(graphiti, *, migrations: Iterable[Migration] | None = None) -> dict:
    """
    Идемпотентно применяет миграции из ./migrations/*.cypher.
    Записывает применённые миграции в узлы (:Migration {migration_id, checksum, applied_at}).

    Applied migration files are immutable: if the same migration_id is present
    with a different checksum (or a legacy ledger row has no checksum), fail
    closed before executing any migration statements.
    """
    await ensure_migrations_table(graphiti)
    all_migs = list(migrations) if migrations is not None else load_migrations()
    if not all_migs:
        return {"applied": 0, "skipped": 0, "total": 0}

    applied = await applied_migrations(graphiti)

    for mig in all_migs:
        if mig.migration_id not in applied:
            continue
        recorded_checksum = applied[mig.migration_id]
        if recorded_checksum != mig.checksum:
            raise RuntimeError(
                "migration checksum mismatch for "
                f"{mig.migration_id}: recorded={recorded_checksum!r} current={mig.checksum!r}"
            )

    driver = graphiti.driver
    applied_count = 0
    skipped = 0

    for mig in all_migs:
        if mig.migration_id in applied:
            skipped += 1
            continue
        for stmt in mig.statements:
            await driver.execute_query(stmt)
        await driver.execute_query(
            """
            CREATE (m:Migration {
              migration_id:$id,
              checksum:$checksum,
              applied_at:$ts
            })
            """,
            id=mig.migration_id,
            checksum=mig.checksum,
            ts=datetime.now(timezone.utc).isoformat(),
        )
        applied_count += 1

    return {"applied": applied_count, "skipped": skipped, "total": len(all_migs)}


