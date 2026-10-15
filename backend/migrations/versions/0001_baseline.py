# backend/migrations/versions/0001_baseline.py
#
# Purpose: BASELINE revision. Recreates the schema exactly as it existed after
# Phase 3B v2 (when tables were still made by create_all):
#   * on a FRESH database, `alembic upgrade head` builds these tables;
#   * on the EXISTING dev database (tables already there), run
#     `alembic stamp 0001` once, which records "already at 0001" without
#     touching any data.
# Never edit this file after it has been applied; add a new revision instead.

"""baseline: users and transactions (Phase 3B v2 schema)

Revision ID: 0001
Revises:
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_id", "users", ["id"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("category_confidence", sa.Float(), nullable=True),
        sa.Column("category_reasoning", sa.String(1000), nullable=True),
        sa.Column("description_key", sa.String(200), nullable=True),
        sa.Column("category_source", sa.String(20), nullable=True),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("transaction_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_transactions_id", "transactions", ["id"])
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])
    op.create_index("ix_transactions_description_key", "transactions", ["description_key"])


def downgrade() -> None:
    op.drop_table("transactions")   # dropping a table also drops its indexes
    op.drop_table("users")
