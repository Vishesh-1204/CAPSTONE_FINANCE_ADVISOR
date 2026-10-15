# backend/migrations/env.py
#
# Purpose: Alembic runs this file for every migration command. It decides
# WHICH database to talk to and WHICH models describe the target schema.
#
# URL priority:
#   1. an URL set programmatically on the Alembic Config (used by the tests
#      to point at a throwaway SQLite database)
#   2. settings.DATABASE_URL from .env (normal use, inside Docker)

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.base_class import Base
# Import every model module so Base.metadata knows all tables
# (needed for `alembic revision --autogenerate` and the drift test).
from app.models import transaction, user  # noqa: F401

config = context.config

# Only configure logging when run from the CLI; the tests call Alembic from
# Python and should keep pytest's logging setup.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url"):
    from app.core.config import settings
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """'alembic upgrade head --sql': prints the SQL instead of running it."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Normal mode: connect to the database and apply migrations."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most columns in place. "Batch mode" makes Alembic
            # copy the table instead, so the same migrations run in the SQLite tests.
            render_as_batch=connection.dialect.name == "sqlite",
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
