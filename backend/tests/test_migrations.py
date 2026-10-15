# tests/test_migrations.py
#
# Purpose: proves the Alembic migrations work on an EMPTY database and that
# they produce exactly the schema the SQLAlchemy models describe.
#
# Why the "drift" test matters: if someone adds a column to a model but forgets
# to write a migration, the tests still pass (they use create_all), but the real
# Postgres database would be missing the column. This test catches that.

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.db.base_class import Base
from app.models import transaction, user  # noqa: F401  (registers the tables)

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _alembic_config(url: str) -> Config:
    """Alembic config pointing at a throwaway database instead of .env's DATABASE_URL."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture()
def db_url(tmp_path) -> str:
    # A file (not :memory:) because Alembic opens several separate connections
    return f"sqlite:///{tmp_path / 'migrations.db'}"


def test_upgrade_head_on_empty_database_creates_all_tables(db_url):
    command.upgrade(_alembic_config(db_url), "head")

    tables = set(inspect(create_engine(db_url)).get_table_names())
    assert {"users", "transactions", "alembic_version"} <= tables


def test_migrations_match_the_models_no_drift(db_url):
    command.upgrade(_alembic_config(db_url), "head")

    engine = create_engine(db_url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)

    assert differences == [], f"Models and migrations disagree; write a migration for: {differences}"


def test_downgrade_to_base_removes_everything(db_url):
    config = _alembic_config(db_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    tables = set(inspect(create_engine(db_url)).get_table_names())
    assert tables <= {"alembic_version"}
