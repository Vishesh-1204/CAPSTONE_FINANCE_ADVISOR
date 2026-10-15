# backend/migrations/versions/0002_statement_ingestion_and_overrides.py
#
# Purpose: Phase 3B v3 schema change.
#   * transactions: direction, txn_type, investment_subtype, channel, payee_key,
#     balance, balance_ok, agent_category, user_locked, import_batch_id
#   * transactions.amount: Float -> Numeric(14,2) (money must not be float)
#   * new tables: import_batches, payee_rules
#
# Existing rows survive: every new column is nullable or has a server default,
# and the backfill below uses plain SQL only. (Importing app code such as the
# type detector into a migration is a trap: when that code changes later, this
# old migration would silently behave differently.) txn_type of old rows stays
# NULL and is filled by the next categorization run.

"""statement ingestion, investment flows, manual override

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(10), nullable=False),
        sa.Column("layout", sa.String(30), nullable=True),
        sa.Column("rows_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_imported", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_duplicate", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_invalid", sa.Integer(), server_default="0", nullable=False),
        sa.Column("balance_checked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("balance_mismatches", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_import_batches_user_id", "import_batches", ["user_id"])

    op.create_table(
        "payee_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("match_type", sa.String(20), nullable=False),
        sa.Column("match_value", sa.String(200), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "match_type", "match_value", name="uq_payee_rules_user_match"),
    )
    op.create_index("ix_payee_rules_user_id", "payee_rules", ["user_id"])

    # batch_alter_table = plain ALTER TABLE on Postgres; table copy on SQLite (tests)
    with op.batch_alter_table("transactions") as batch:
        batch.alter_column(
            "amount",
            existing_type=sa.Float(),
            type_=sa.Numeric(14, 2),
            existing_nullable=False,
            postgresql_using="round(amount::numeric, 2)",
        )
        batch.add_column(sa.Column("direction", sa.String(6), server_default="debit", nullable=False))
        batch.add_column(sa.Column("txn_type", sa.String(20), nullable=True))
        batch.add_column(sa.Column("investment_subtype", sa.String(20), nullable=True))
        batch.add_column(sa.Column("channel", sa.String(10), nullable=True))
        batch.add_column(sa.Column("payee_key", sa.String(200), nullable=True))
        batch.add_column(sa.Column("balance", sa.Numeric(14, 2), nullable=True))
        batch.add_column(sa.Column("balance_ok", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("agent_category", sa.String(100), nullable=True))
        batch.add_column(sa.Column("user_locked", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("import_batch_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_transactions_import_batch_id", "import_batches", ["import_batch_id"], ["id"]
        )
        batch.create_index("ix_transactions_import_batch_id", ["import_batch_id"])
        batch.create_index("ix_transactions_user_id_payee_key", ["user_id", "payee_key"])

    # --- Backfill existing rows (plain SQL, see header) ---
    transactions = sa.table(
        "transactions",
        sa.column("category", sa.String),
        sa.column("category_source", sa.String),
        sa.column("agent_category", sa.String),
        sa.column("user_locked", sa.Boolean),
    )
    # Before v3, "the user set it" was recorded as category_source='user': lock those rows
    op.execute(transactions.update().where(transactions.c.category_source == "user").values(user_locked=True))
    # Rows the agent categorized: its answer IS the original suggestion
    op.execute(
        transactions.update()
        .where(
            transactions.c.category.is_not(None),
            sa.or_(transactions.c.category_source.is_(None), transactions.c.category_source != "user"),
        )
        .values(agent_category=transactions.c.category)
    )
    # direction needs no backfill: its server default 'debit' fills existing rows,
    # and before v3 credits were never imported.


def downgrade() -> None:
    # WARNING: lossy. Once `direction` is dropped, any imported CREDIT rows look
    # like expenses again. Only downgrade a development database.
    with op.batch_alter_table("transactions") as batch:
        batch.drop_index("ix_transactions_user_id_payee_key")
        batch.drop_index("ix_transactions_import_batch_id")
        batch.drop_constraint("fk_transactions_import_batch_id", type_="foreignkey")
        for column in (
            "import_batch_id", "user_locked", "agent_category", "balance_ok", "balance",
            "payee_key", "channel", "investment_subtype", "txn_type", "direction",
        ):
            batch.drop_column(column)
        batch.alter_column(
            "amount", existing_type=sa.Numeric(14, 2), type_=sa.Float(), existing_nullable=False,
            postgresql_using="amount::double precision",
        )
    op.drop_table("payee_rules")
    op.drop_table("import_batches")
