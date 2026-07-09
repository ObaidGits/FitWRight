import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# ``disable_existing_loggers=False`` is important: env.py can be invoked
# in-process (e.g. the migration test suite runs the chain via Alembic's command
# API), and the default fileConfig behavior would otherwise disable every logger
# not named in alembic.ini — silently muting the rest of the app's loggers.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Model metadata for 'autogenerate' support. ``Base`` aggregates every ORM
# table (the document tables plus the auth tables); the DB-backed KVStore ``kv``
# table is intentionally *not* on ``Base`` — it is owned by the KVStore adapter
# and declared in migration 0002 for hosted Postgres (see design "Data Models").
from app.models import Base  # noqa: E402

target_metadata = Base.metadata


def _resolve_database_url() -> str:
    """Resolve the database URL Alembic should run against.

    Precedence (single source of truth so migrations never hit the ini
    placeholder): an explicit ``-x db_url=…`` passed on the command line, then
    the ``ALEMBIC_DATABASE_URL`` environment variable (used by the migration
    test harness to point at a throwaway copy), then the application's resolved
    ``effective_database_url`` (SQLite locally, Postgres hosted — ADR-13). The
    async driver is required because ``env.py`` runs migrations through an async
    engine.
    """
    x_args = context.get_x_argument(as_dictionary=True)
    url = x_args.get("db_url") or os.environ.get("ALEMBIC_DATABASE_URL")
    if not url:
        from app.config import settings

        url = settings.effective_database_url
    # Normalize to an async driver so ``async_engine_from_config`` can connect.
    if url.startswith("sqlite:///"):
        url = url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


# Inject the resolved URL so both offline and online paths use it instead of the
# ini placeholder.
config.set_main_option("sqlalchemy.url", _resolve_database_url())

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # ``render_as_batch`` makes autogenerate emit SQLite-safe batch ALTERs
    # (SQLite cannot ALTER most constraints in place). Hand-written migrations
    # already use ``batch_alter_table`` where needed; this only affects diffs.
    is_sqlite = connection.dialect.name == "sqlite"
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=is_sqlite,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
