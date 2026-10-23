# backend/migrations/versions/0005_investing.py
#
# Purpose: Phase 6 (Investment Advisor Agent) schema.
#   * investment_profiles - one per user: questionnaire, finance facts, risk profile
#   * holdings            - rupees per asset class (manual), CHECK on the class and amount
#   * investment_plans    - one row per generated plan, with the explanation research log
#
# This revision only ADDS tables, so existing users, transactions, budgets and
# forecasts are untouched. Like 0002-0004 it uses plain SQLAlchemy/Alembic calls
# only (no app imports), so it keeps behaving the same when the app code changes.
# That is also why the asset-class list is written out here instead of imported.

"""investing

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _money(name: str, **kwargs) -> sa.Column:
    """A required Numeric(14,2) money column."""
    return sa.Column(name, sa.Numeric(14, 2), nullable=False, **kwargs)


def upgrade() -> None:
    op.create_table(
        "investment_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tolerance_answers", sa.JSON(), nullable=False),
        sa.Column("capacity_facts", sa.JSON(), nullable=False),
        _money("liquid_savings"),
        _money("existing_monthly_sip", server_default="0"),
        sa.Column("tolerance_score", sa.Numeric(5, 1), nullable=False),
        sa.Column("capacity_score", sa.Numeric(5, 1), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("limiting_factor", sa.String(10), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_investment_profiles_user_id"),
    )

    op.create_table(
        "holdings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("asset_class", sa.String(10), nullable=False),
        _money("amount"),
        sa.Column("label", sa.String(100), server_default="", nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.UniqueConstraint("user_id", "asset_class", "label", name="uq_holdings_user_class_label"),
        sa.CheckConstraint("asset_class IN ('equity', 'debt', 'gold', 'cash')", name="ck_holdings_asset_class"),
        sa.CheckConstraint("amount >= 0", name="ck_holdings_amount_non_negative"),
    )

    op.create_table(
        "investment_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("assumptions_json", sa.JSON(), nullable=False),
        sa.Column("engine_version", sa.String(20), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("llm_used", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("explanation_faithful", sa.Boolean(), nullable=True),
        sa.Column("claims_compliant", sa.Boolean(), nullable=True),
        sa.Column("llm_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("raw_prohibited_claims", sa.Integer(), server_default="0", nullable=False),
    )
    # Newest-first per user: what /plan/latest and /plans query
    op.create_index(
        "ix_investment_plans_user_id_created_at", "investment_plans", ["user_id", sa.text("created_at DESC")]
    )


def downgrade() -> None:
    # Drops every profile, holding and saved plan. Only downgrade a development database.
    op.drop_index("ix_investment_plans_user_id_created_at", table_name="investment_plans")
    op.drop_table("investment_plans")
    op.drop_table("holdings")
    op.drop_table("investment_profiles")
