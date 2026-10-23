# backend/migrations/versions/0004_forecast_runs.py
#
# Purpose: Phase 5 (Financial Forecast Agent) schema.
#   * forecast_runs - one row per generated forecast (headline numbers with
#     their interval, daily trajectory, recurring items, budget warnings)
#
# This revision only ADDS a table, so existing users, transactions and budgets
# are untouched. Like 0002/0003 it uses plain SQLAlchemy/Alembic calls only
# (no app imports), so it keeps behaving the same when the app code changes.

"""forecast runs

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _money(name: str) -> sa.Column:
    """A required Numeric(14,2) money column."""
    return sa.Column(name, sa.Numeric(14, 2), nullable=False)


def upgrade() -> None:
    op.create_table(
        "forecast_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(40), nullable=False),
        sa.Column("history_months", sa.Integer(), nullable=False),
        sa.Column("low_confidence", sa.Boolean(), nullable=False),
        _money("opening_balance"),
        sa.Column("opening_balance_source", sa.String(10), nullable=False),
        sa.Column("opening_balance_date", sa.Date(), nullable=True),
        sa.Column("income_source", sa.String(10), nullable=False),
        _money("expected_income"),
        _money("expected_fixed_expenses"),
        _money("expected_variable_expenses"),
        _money("expected_variable_expenses_lower"),
        _money("expected_variable_expenses_upper"),
        _money("expected_investments"),
        _money("net_cashflow"),
        _money("projected_end_balance"),
        _money("projected_end_balance_lower"),
        _money("projected_end_balance_upper"),
        sa.Column("cash_crunch_date", sa.Date(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("llm_used", sa.Boolean(), nullable=False),
        sa.Column("explanation_faithful", sa.Boolean(), nullable=True),
        sa.Column("trajectory", sa.JSON(), nullable=False),
        sa.Column("recurring_items", sa.JSON(), nullable=False),
        sa.Column("budget_warnings", sa.JSON(), nullable=False),
        sa.Column("model_scores", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_forecast_runs_user_id", "forecast_runs", ["user_id"])


def downgrade() -> None:
    # Drops every saved forecast. Only downgrade a development database.
    op.drop_index("ix_forecast_runs_user_id", table_name="forecast_runs")
    op.drop_table("forecast_runs")
