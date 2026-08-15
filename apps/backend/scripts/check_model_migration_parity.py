"""Verify the new ORM models match what the migrations actually create.

A model/migration mismatch is silent: the app works locally (tables built from
metadata) and fails in production (tables built by Alembic), or vice versa. This
compares column names and nullability for the six new tables.

Run: uv run python scripts/check_model_migration_parity.py
"""

import asyncio
import os
import sqlite3
import subprocess
import sys
import tempfile

TABLES = [
    "ai_channels",
    "ai_channel_health",
    "ai_usage_ledger",
    "credit_accounts",
    "credit_reservations",
    "credit_transactions",
]


def columns_from_sqlite(db_path: str, table: str) -> dict[str, bool]:
    """Return {column_name: notnull} for ``table``."""
    con = sqlite3.connect(db_path)
    try:
        rows = list(con.execute(f"PRAGMA table_info({table})"))
    finally:
        con.close()
    return {r[1]: bool(r[3]) for r in rows}


async def build_from_models(db_path: str) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.models import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


def build_from_migrations(db_path: str) -> None:
    env = dict(os.environ, ALEMBIC_DATABASE_URL=f"sqlite+aiosqlite:///{db_path}")
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        check=True,
        capture_output=True,
        env=env,
    )


def main() -> int:
    tmp = tempfile.mkdtemp()
    model_db = os.path.join(tmp, "models.db")
    mig_db = os.path.join(tmp, "migrations.db")

    asyncio.run(build_from_models(model_db))
    build_from_migrations(mig_db)

    problems: list[str] = []
    for table in TABLES:
        from_models = columns_from_sqlite(model_db, table)
        from_migration = columns_from_sqlite(mig_db, table)

        if not from_models:
            problems.append(f"{table}: absent in models")
            continue
        if not from_migration:
            problems.append(f"{table}: absent in migrations")
            continue

        only_models = set(from_models) - set(from_migration)
        only_migration = set(from_migration) - set(from_models)
        if only_models:
            problems.append(f"{table}: in models but NOT migrations: {sorted(only_models)}")
        if only_migration:
            problems.append(f"{table}: in migrations but NOT models: {sorted(only_migration)}")

        for col in sorted(set(from_models) & set(from_migration)):
            if from_models[col] != from_migration[col]:
                problems.append(
                    f"{table}.{col}: nullability differs "
                    f"(models notnull={from_models[col]}, migration notnull={from_migration[col]})"
                )

        if not only_models and not only_migration:
            print(f"  OK {table} ({len(from_models)} columns)")

    if problems:
        print("\nPARITY PROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nAll six tables match between models and migrations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
