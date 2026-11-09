# app/models/stock.py
#
# Purpose: the five Stock Intelligence Agent tables (Phase 7, docs/phase7-spec.md section 4).
#   * `watchlist_items` - USER data: which symbols a user follows (per-user limit in settings).
#   * `price_bars`      - SHARED public market data: one daily bar per (symbol, date). It is a
#                         cache of what a provider returned, so it has no user_id.
#   * `news_items`      - SHARED: headline + link (+ its sentiment), never the article body.
#   * `stock_analyses`  - USER data: one stored analysis with its inputs, results and the
#                         explanation research log (same columns as investment_plans).
#   * `provider_calls`  - one row per call to an outside data provider, so /status can show
#                         the quota used today and the paper can report provider availability.
# Prices are Numeric(14,4), never Float (exact values; 4 decimals cover paise and index levels).
# Schema changes need an Alembic revision in migrations/versions/.
#
# Note: `PriceBar` here is the DATABASE row. market_data/base.py (7A-3) has a frozen dataclass
# with the same name for what an adapter returns; the cache service converts one to the other.

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, Numeric,
    String, Text, UniqueConstraint, false, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.stock_symbols import MAX_SYMBOL_LENGTH
from app.db.base_class import Base

# Fixed value lists. The schema Literals in schemas/stock.py must match these
# (tests/test_stocks_data_model.py checks it).
PRICE_SOURCES: tuple[str, ...] = ("yfinance", "alphavantage", "csv_upload")
NEWS_SOURCES: tuple[str, ...] = ("google_rss", "yfinance")
SCORER_NAMES: tuple[str, ...] = ("lexicon", "finbert")
# "ok" plus the four ProviderError kinds from spec section 8. Enforced with a CHECK rule
# (a short string + CHECK, not a native Postgres ENUM, which is awkward to migrate).
PROVIDER_CALL_STATUSES: tuple[str, ...] = ("ok", "rate_limited", "unavailable", "not_found", "bad_response")

EXPLANATION_FROM_LLM = "llm"
EXPLANATION_FROM_TEMPLATE = "template"


def _symbol_column() -> Mapped[str]:
    """Every table stores symbols already normalised by core.stock_symbols (upper case, <= 20 chars)."""
    return mapped_column(String(MAX_SYMBOL_LENGTH), nullable=False)


def _price_column() -> Mapped[Decimal]:
    return mapped_column(Numeric(14, 4), nullable=False)


class WatchlistItem(Base):
    """One symbol a user follows. The per-user limit (STOCK_WATCHLIST_LIMIT) is enforced in the service."""
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", name="uq_watchlist_items_user_symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # No separate user_id index: the unique constraint's index starts with user_id
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = _symbol_column()
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PriceBar(Base):
    """One daily OHLC bar for a symbol, as the provider (or a CSV upload) gave it. Shared by all users."""
    __tablename__ = "price_bars"
    __table_args__ = (
        # The upsert key: refreshing the same day again updates the row instead of duplicating it.
        # Its index (symbol, bar_date) also serves "this symbol's bars between two dates".
        UniqueConstraint("symbol", "bar_date", name="uq_price_bars_symbol_bar_date"),
        # Last line of defence against a broken provider row (the adapter should drop these first)
        CheckConstraint("open > 0 AND high > 0 AND low > 0 AND close > 0", name="ck_price_bars_prices_positive"),
        CheckConstraint("low <= high", name="ck_price_bars_low_not_above_high"),
        CheckConstraint("volume IS NULL OR volume >= 0", name="ck_price_bars_volume_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = _symbol_column()
    bar_date: Mapped[date] = mapped_column(Date, nullable=False)
    open: Mapped[Decimal] = _price_column()
    high: Mapped[Decimal] = _price_column()
    low: Mapped[Decimal] = _price_column()
    close: Mapped[Decimal] = _price_column()
    # BigInteger: index volumes can pass the 32-bit limit (about 2.1 billion). NULL = provider gave none.
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False)      # one of PRICE_SOURCES
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NewsItemRow(Base):
    """One headline about a symbol: text, link, publisher, time and (once scored) its sentiment. Shared."""
    __tablename__ = "news_items"
    __table_args__ = (
        # The same article seen again on the next refresh is not stored twice
        UniqueConstraint("symbol", "url", name="uq_news_items_symbol_url"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = _symbol_column()
    headline: Mapped[str] = mapped_column(String(500), nullable=False)
    # Google News links are long redirect URLs; 2048 still fits a Postgres btree index entry
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    publisher: Mapped[str | None] = mapped_column(String(200), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)      # one of NEWS_SOURCES

    # Probabilities from the scorer (they add up to 1). NULL until the headline is scored.
    # Float is fine here: these are model outputs, not money or prices.
    sentiment_positive: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment_negative: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment_neutral: Mapped[float | None] = mapped_column(Float, nullable=True)
    scorer_name: Mapped[str | None] = mapped_column(String(20), nullable=True)   # one of SCORER_NAMES

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StockAnalysis(Base):
    """One generated analysis, stored so it can be reopened, compared over time and measured."""
    __tablename__ = "stock_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Indexed by the composite (user_id, created_at DESC) index below
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    symbol: Mapped[str] = _symbol_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # What the analysis was built from (windows, benchmark, settings) and what it produced
    # (indicators, risk, sentiment, warnings). The facts given to the LLM live in result_json too.
    inputs_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Date of the newest price bar used: the UI always shows "as of" this date
    data_as_of: Mapped[date] = mapped_column(Date, nullable=False)
    # Where each input came from, e.g. {"prices": "yfinance", "news": "google_rss", "cache": "fresh"}
    sources_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(20), nullable=False)

    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Research log (same meaning as in investment_plans). The two guard columns are NULL when
    # there was no LLM text to check.
    llm_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    explanation_faithful: Mapped[bool | None] = mapped_column(Boolean, nullable=True)   # numeric guard passed
    claims_compliant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)       # claims guard v3 passed
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Prohibited phrases found in the RAW LLM text, before the guard swapped it out
    raw_prohibited_claims: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    @property
    def explanation_source(self) -> str:
        """'llm' only when LLM text was used AND passed both guards; otherwise the template was shown."""
        if self.llm_used and self.explanation_faithful and self.claims_compliant:
            return EXPLANATION_FROM_LLM
        return EXPLANATION_FROM_TEMPLATE


class ProviderCall(Base):
    """One request to an outside provider (successful or not). Not user data: no user_id."""
    __tablename__ = "provider_calls"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{name}'" for name in PROVIDER_CALL_STATUSES) + ")",
            name="ck_provider_calls_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)    # e.g. "yfinance", "google_rss"
    endpoint: Mapped[str] = mapped_column(String(50), nullable=False)    # e.g. "daily_prices", "news"
    status: Mapped[str] = mapped_column(String(20), nullable=False)      # one of PROVIDER_CALL_STATUSES
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# /analysis/latest and /analyses read "this user's analyses, newest first" (spec section 4)
Index("ix_stock_analyses_user_id_created_at", StockAnalysis.user_id, StockAnalysis.created_at.desc())
# /status counts "calls to this provider since midnight"
Index("ix_provider_calls_provider_at", ProviderCall.provider, ProviderCall.at)
