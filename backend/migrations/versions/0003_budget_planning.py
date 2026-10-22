# backend/migrations/versions/0003_budget_planning.py
#
# Purpose: Phase 4 (Budget Planning Agent) schema.
#   * financial_profiles - income + savings target, one per user
#   * budgets            - monthly limit per (user, month, category)
#   * budget_runs        - research log, one row per plan generation
#
# This revision only ADDS tables, so existing users and transactions are
# untouched. Like 0002, it uses plain SQLAlchemy/Alembic calls only (no app
# imports), so it keeps behaving the same when the app code changes later.

"""budget planning: financial profiles, budgets, budget runs

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "financial_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("monthly_income", sa.Numeric(14, 2), nullable=False),
        sa.Column("savings_target_pct", sa.Numeric(5, 2), server_default="20", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_financial_profiles_user_id"),
    )

    op.create_table(
        "budgets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("limit_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("agent_limit_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("source", sa.String(10), server_default="agent", nullable=False),
        sa.Column("reasoning", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "month", "category", name="uq_budgets_user_month_category"),
    )
    op.create_index("ix_budgets_user_id", "budgets", ["user_id"])

    op.create_table(
        "budget_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("spendable", sa.Numeric(14, 2), nullable=False),
        sa.Column("feasible", sa.Boolean(), nullable=False),
        sa.Column("shortfall", sa.Numeric(14, 2), nullable=False),
        sa.Column("months_used", sa.Integer(), nullable=False),
        sa.Column("llm_used", sa.Boolean(), nullable=False),
        sa.Column("explanation_faithful", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_budget_runs_user_id", "budget_runs", ["user_id"])


def downgrade() -> None:
    # Drops all budgets and profiles. Only downgrade a development database.
    op.drop_index("ix_budget_runs_user_id", table_name="budget_runs")
    op.drop_table("budget_runs")
    op.drop_index("ix_budgets_user_id", table_name="budgets")
    op.drop_table("budgets")
    op.drop_table("financial_profiles")
