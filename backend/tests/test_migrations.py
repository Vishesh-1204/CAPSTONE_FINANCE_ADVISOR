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
import app.models  # noqa: F401  (registers every table)

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


def test_0002_upgrades_existing_rows_without_losing_data(db_url):
    """Simulates the real dev database: rows created at 0001, then upgraded to 0002."""
    config = _alembic_config(db_url)
    command.upgrade(config, "0001")

    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users (id, email, full_name, hashed_password, is_active) VALUES (1, 'a@x.com', 'A', 'x', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO transactions (user_id, description, amount, category, category_source, needs_review, "
            "transaction_date) VALUES "
            "(1, 'Swiggy', 450.5, 'Food & Dining', 'rule', 0, '2026-09-01'), "
            "(1, 'Raj Mess', 3000, 'Groceries', 'user', 0, '2026-09-02'), "
            "(1, 'Unknown', 10, NULL, NULL, 0, '2026-09-03')"
        )

    command.upgrade(config, "head")

    with engine.connect() as connection:
        rows = connection.exec_driver_sql(
            "SELECT description, amount, direction, user_locked, agent_category, txn_type "
            "FROM transactions ORDER BY id"
        ).all()
    swiggy, mess, unknown = rows
    assert float(swiggy.amount) == 450.5 and swiggy.direction == "debit"
    assert swiggy.agent_category == "Food & Dining" and not swiggy.user_locked     # agent decided it
    assert mess.user_locked and mess.agent_category is None                      # the user decided it
    assert unknown.agent_category is None and not unknown.user_locked
    assert all(row.txn_type is None for row in rows)          # filled by the next categorize run


def test_0003_keeps_existing_data_and_adds_budget_tables(db_url):
    """The real dev database is at 0002 with data; 0003 must only ADD the budget tables."""
    config = _alembic_config(db_url)
    command.upgrade(config, "0002")

    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users (id, email, full_name, hashed_password, is_active) VALUES (1, 'a@x.com', 'A', 'x', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO transactions (user_id, description, amount, direction, txn_type, category, "
            "needs_review, user_locked, transaction_date) VALUES "
            "(1, 'Swiggy', 450.50, 'debit', 'expense', 'Food & Dining', 0, 0, '2026-09-01')"
        )

    command.upgrade(config, "head")

    tables = set(inspect(engine).get_table_names())
    assert {"financial_profiles", "budgets", "budget_runs"} <= tables
    with engine.begin() as connection:
        row = connection.exec_driver_sql("SELECT description, amount, category FROM transactions").one()
        assert row.description == "Swiggy" and float(row.amount) == 450.5 and row.category == "Food & Dining"
        # server defaults: a profile without a savings target gets 20%, a budget gets source 'agent'
        connection.exec_driver_sql("INSERT INTO financial_profiles (user_id, monthly_income) VALUES (1, 60000)")
        connection.exec_driver_sql(
            "INSERT INTO budgets (user_id, month, category, limit_amount, agent_limit_amount) "
            "VALUES (1, '2026-10', 'Groceries', 5000, 5000)"
        )
        assert float(connection.exec_driver_sql("SELECT savings_target_pct FROM financial_profiles").scalar()) == 20
        assert connection.exec_driver_sql("SELECT source FROM budgets").scalar() == "agent"


def test_0004_keeps_existing_data_and_adds_forecast_runs(db_url):
    """The real dev database is at 0003 with data; 0004 must only ADD forecast_runs."""
    config = _alembic_config(db_url)
    command.upgrade(config, "0003")

    engine = create_engine(db_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO users (id, email, full_name, hashed_password, is_active) VALUES (1, 'a@x.com', 'A', 'x', 1)"
        )
        connection.exec_driver_sql(
            "INSERT INTO transactions (user_id, description, amount, direction, txn_type, category, "
            "needs_review, user_locked, balance, transaction_date) VALUES "
            "(1, 'Swiggy', 450.50, 'debit', 'expense', 'Food & Dining', 0, 0, 10000.25, '2026-09-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO budgets (user_id, month, category, limit_amount, agent_limit_amount) "
            "VALUES (1, '2026-10', 'Groceries', 5000, 5000)"
        )

    command.upgrade(config, "head")

    assert "forecast_runs" in set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        row = connection.exec_driver_sql("SELECT amount, balance FROM transactions").one()
        assert float(row.amount) == 450.5 and float(row.balance) == 10000.25
        assert float(connection.exec_driver_sql("SELECT limit_amount FROM budgets").scalar()) == 5000
        # The new table accepts a run and gives it a created_at
        connection.exec_driver_sql(
            "INSERT INTO forecast_runs (user_id, as_of, horizon_days, model_name, history_months, low_confidence, "
            "opening_balance, opening_balance_source, income_source, expected_income, expected_fixed_expenses, "
            "expected_variable_expenses, expected_variable_expenses_lower, expected_variable_expenses_upper, "
            "expected_investments, net_cashflow, projected_end_balance, projected_end_balance_lower, "
            "projected_end_balance_upper, summary, llm_used, trajectory, recurring_items, budget_warnings, "
            "model_scores) VALUES (1, '2026-10-22', 30, 'moving_average_3', 3, 1, 10000, 'statement', 'profile', "
            "50000, 18000, 20000, 15000, 25000, 3000, 9000, 19000, 14000, 24000, 'text', 0, '[]', '[]', '[]', '[]')"
        )
        assert connection.exec_driver_sql("SELECT created_at FROM forecast_runs").scalar() is not None
