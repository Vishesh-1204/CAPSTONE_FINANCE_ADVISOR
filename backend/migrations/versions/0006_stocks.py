# backend/migrations/versions/0006_stocks.py
#
# Purpose: Phase 7 (Stock Intelligence Agent) schema.
#   * watchlist_items - per-user symbols, UNIQUE (user_id, symbol)
#   * price_bars      - shared daily bars cache, Numeric(14,4), UNIQUE (symbol, bar_date), CHECK rules
#   * news_items      - shared headlines (+ sentiment), UNIQUE (symbol, url)
#   * stock_analyses  - stored analyses with the explanation research log, index (user_id, created_at DESC)
#   * provider_calls  - provider call log, CHECK on status, index (provider, at)
#
# This revision only ADDS tables, so existing users, transactions, budgets, forecasts and
# investing data are untouched. Like 0002-0005 it uses plain SQLAlchemy/Alembic calls only
# (no app imports), so it keeps behaving the same when the app code changes. That is also
# why the symbol length (20) and the status list are written out here instead of imported.

"""stocks

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _symbol() -> sa.Column:
    return sa.Column("symbol", sa.String(20), nullable=False)


def _price(name: str) -> sa.Column:
    """A required Numeric(14,4) price column (never Float)."""
    return sa.Column(name, sa.Numeric(14, 4), nullable=False)


def _now(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        _symbol(),
        _now("added_at"),
        sa.UniqueConstraint("user_id", "symbol", name="uq_watchlist_items_user_symbol"),
    )

    op.create_table(
        "price_bars",
        sa.Column("id", sa.Integer(), primary_key=True),
        _symbol(),
        sa.Column("bar_date", sa.Date(), nullable=False),
        _price("open"),
        _price("high"),
        _price("low"),
        _price("close"),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        _now("fetched_at"),
        sa.UniqueConstraint("symbol", "bar_date", name="uq_price_bars_symbol_bar_date"),
        sa.CheckConstraint("open > 0 AND high > 0 AND low > 0 AND close > 0", name="ck_price_bars_prices_positive"),
        sa.CheckConstraint("low <= high", name="ck_price_bars_low_not_above_high"),
        sa.CheckConstraint("volume IS NULL OR volume >= 0", name="ck_price_bars_volume_non_negative"),
    )

    op.create_table(
        "news_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        _symbol(),
        sa.Column("headline", sa.String(500), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("publisher", sa.String(200), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("sentiment_positive", sa.Float(), nullable=True),
        sa.Column("sentiment_negative", sa.Float(), nullable=True),
        sa.Column("sentiment_neutral", sa.Float(), nullable=True),
        sa.Column("scorer_name", sa.String(20), nullable=True),
        _now("fetched_at"),
        sa.UniqueConstraint("symbol", "url", name="uq_news_items_symbol_url"),
    )

    op.create_table(
        "stock_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        _symbol(),
        _now("created_at"),
        sa.Column("inputs_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("data_as_of", sa.Date(), nullable=False),
        sa.Column("sources_json", sa.JSON(), nullable=False),
        sa.Column("engine_version", sa.String(20), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("llm_used", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("explanation_faithful", sa.Boolean(), nullable=True),
        sa.Column("claims_compliant", sa.Boolean(), nullable=True),
        sa.Column("llm_calls", sa.Integer(), server_default="0", nullable=False),
        sa.Column("raw_prohibited_claims", sa.Integer(), server_default="0", nullable=False),
    )
    # Newest-first per user: what /analysis/latest and /analyses query
    op.create_index(
        "ix_stock_analyses_user_id_created_at", "stock_analyses", ["user_id", sa.text("created_at DESC")]
    )

    op.create_table(
        "provider_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("endpoint", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _now("at"),
        sa.CheckConstraint(
            "status IN ('ok', 'rate_limited', 'unavailable', 'not_found', 'bad_response')",
            name="ck_provider_calls_status",
        ),
    )
    # "Calls to this provider today" for /status
    op.create_index("ix_provider_calls_provider_at", "provider_calls", ["provider", "at"])


def downgrade() -> None:
    # Drops the watchlists, the cached market data and every stored analysis.
    # Only downgrade a development database (the cache can be refilled with seed_market_cache).
    op.drop_index("ix_provider_calls_provider_at", table_name="provider_calls")
    op.drop_table("provider_calls")
    op.drop_index("ix_stock_analyses_user_id_created_at", table_name="stock_analyses")
    op.drop_table("stock_analyses")
    op.drop_table("news_items")
    op.drop_table("price_bars")
    op.drop_table("watchlist_items")
