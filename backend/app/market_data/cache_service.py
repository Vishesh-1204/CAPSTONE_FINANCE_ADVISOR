# app/market_data/cache_service.py
#
# Purpose: the ONLY way the app reads market data. Public prices and headlines are cached in
# the database and the provider is called at most once per symbol per TTL (spec section 5.2).
#
# Fallback chain for prices (spec section 5.3), in this exact order:
#   1. fresh cache   -> newest fetch for the symbol is younger than STOCK_CACHE_TTL_HOURS and
#                       no market close has happened since    -> cache_status "fresh"
#   2. provider      -> fetch the full STOCK_HISTORY_DAYS window, upsert on (symbol, bar_date)
#                                                               -> cache_status "refreshed"
#   3. stale cache   -> provider failed, older cached bars exist -> cache_status "stale" + warning
#                       "data_stale"
#   4. nothing       -> NoMarketDataError, whose message points to the CSV upload endpoint
# Headlines follow the same chain, except step 4 is not an error: an analysis can run without
# news, so it returns no headlines with the warning "news_unavailable".
#
# Every provider call (success or failure) is logged in provider_calls.
# Why always the FULL window: one request per symbol per TTL, the cache always covers any
# `days` up to the window, and a stock split (which rewrites older split-adjusted prices)
# reaches every cached bar, not just the newest ones.

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.stock_symbols import normalize_symbol
from app.market_data.base import (
    CLOSE_FINAL_AFTER_IST, IST, NewsItem, NewsProvider, PriceBar, PriceProvider, ProviderError,
)
from app.models.stock import NewsItemRow, PriceBar as PriceBarRow, ProviderCall

logger = logging.getLogger(__name__)

# After CLOSE_FINAL_AFTER_IST, a cache filled earlier in the day is no longer "fresh":
# it is missing today's closing bar.

DATA_STALE = "data_stale"
CSV_UPLOAD = "csv_upload"            # price_bars.source for user-uploaded bars
# Failures after which the provider is left alone for STOCK_PROVIDER_COOLDOWN_MINUTES
COOLDOWN_STATUSES = ("rate_limited", "unavailable")
NEWS_STALE = "news_stale"
NEWS_UNAVAILABLE = "news_unavailable"
DEFAULT_NEWS_LIMIT = 30
UPSERT_CHUNK = 500          # rows per INSERT ... ON CONFLICT statement

CacheStatus = Literal["fresh", "refreshed", "stale"]


class NoMarketDataError(Exception):
    """Step 4 of the chain: the provider failed AND nothing is cached for this symbol."""

    def __init__(self, symbol: str, kind: str) -> None:
        reason = ("nothing is cached yet; refresh it first" if kind == "not_cached"
                  else f"the market-data provider failed ({kind}) and nothing is cached yet")
        super().__init__(
            f"No price data for {symbol}: {reason}. Try again later, or upload a CSV of daily prices "
            f"with POST /api/stocks/{symbol}/prices/upload."
        )
        self.symbol = symbol
        self.kind = kind


@dataclass(frozen=True)
class PriceResult:
    """What get_prices returns (spec section 8), plus how the cache answered."""
    symbol: str
    bars: list[PriceBar]                  # oldest first, bar_date >= today - days
    source: str | None                    # source of the newest bar: "yfinance" | "csv_upload" ...
    data_as_of: date | None               # newest cached bar date (even if outside `days`)
    cache_status: CacheStatus
    warnings: list[str] = field(default_factory=list)
    provider_error: str | None = None     # ProviderError kind when the chain fell back to stale
    saved: int = 0                        # bars written by THIS call (0 unless refreshed)


@dataclass(frozen=True)
class NewsResult:
    symbol: str
    items: list[NewsItem]                 # newest first
    source: str | None
    cache_status: CacheStatus | None      # None when there is nothing at all
    warnings: list[str] = field(default_factory=list)
    provider_error: str | None = None
    saved: int = 0                        # headlines written by THIS call


# --- Time helpers ---

def _utc(moment: datetime) -> datetime:
    """SQLite gives back naive datetimes; everything here is written in UTC, so treat naive as UTC."""
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def latest_close_cutoff(now: datetime) -> datetime:
    """The most recent weekday 16:00 IST at or before `now` (holidays are not modelled)."""
    local = _utc(now).astimezone(IST)
    day = local.date()
    if local.time() < CLOSE_FINAL_AFTER_IST:
        day -= timedelta(days=1)
    while day.weekday() >= 5:              # Saturday, Sunday -> back to Friday
        day -= timedelta(days=1)
    return datetime.combine(day, CLOSE_FINAL_AFTER_IST, tzinfo=IST).astimezone(timezone.utc)


def is_fresh(last_fetch: datetime | None, now: datetime, ttl_hours: float) -> bool:
    """Fresh = fetched within the TTL AND after the latest market close (so it has that close)."""
    if last_fetch is None:
        return False
    last_fetch, now = _utc(last_fetch), _utc(now)
    return now - last_fetch < timedelta(hours=ttl_hours) and last_fetch >= latest_close_cutoff(now)


# --- Small database helpers ---

def _log_call(db: Session, provider: str, endpoint: str, status: str, now: datetime) -> None:
    db.add(ProviderCall(provider=provider, endpoint=endpoint, status=status, at=_utc(now)))


def _insert_for(db: Session):
    """INSERT ... ON CONFLICT exists in both Postgres (the app) and SQLite (the tests)."""
    return postgres_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert


def upsert_bars(db: Session, bars: list[PriceBar], source: str, now: datetime, *, overwrite: bool = True) -> int:
    """
    Insert or update on (symbol, bar_date): the same day twice is ONE row with the newest values.

    overwrite=False (CSV uploads) only ADDS missing days and never replaces a cached bar:
    price_bars is shared by all users, so one user's file must not change another user's data.
    """
    insert = _insert_for(db)
    rows = [
        {"symbol": b.symbol, "bar_date": b.bar_date, "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume, "source": source, "fetched_at": _utc(now)}
        for b in bars
    ]
    for start in range(0, len(rows), UPSERT_CHUNK):
        statement = insert(PriceBarRow).values(rows[start:start + UPSERT_CHUNK])
        if overwrite:
            statement = statement.on_conflict_do_update(
                index_elements=["symbol", "bar_date"],
                set_={name: statement.excluded[name]
                      for name in ("open", "high", "low", "close", "volume", "source", "fetched_at")},
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=["symbol", "bar_date"])
        db.execute(statement)
    return len(rows)


def upsert_news(db: Session, items: list[NewsItem], source: str, now: datetime) -> int:
    """Insert or update on (symbol, url). An existing row keeps its sentiment scores."""
    if not items:
        return 0
    insert = _insert_for(db)
    rows = [
        {"symbol": i.symbol, "headline": i.headline, "url": i.url, "publisher": i.publisher,
         "published_at": _utc(i.published_at), "source": source, "fetched_at": _utc(now)}
        for i in items
    ]
    statement = insert(NewsItemRow).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["symbol", "url"],
        set_={name: statement.excluded[name] for name in ("headline", "publisher", "published_at", "fetched_at")},
    )
    db.execute(statement)
    return len(rows)


def _cached_bars(db: Session, symbol: str, since: date) -> list[PriceBar]:
    rows = db.scalars(
        select(PriceBarRow).where(PriceBarRow.symbol == symbol, PriceBarRow.bar_date >= since)
        .order_by(PriceBarRow.bar_date)
    ).all()
    return [PriceBar(r.symbol, r.bar_date, r.open, r.high, r.low, r.close, r.volume) for r in rows]


def _price_cache_state(db: Session, symbol: str) -> tuple[datetime | None, date | None, str | None]:
    """
    (newest PROVIDER fetch, newest bar_date of any source, source of the newest bar).

    CSV uploads do not count as a fetch: they are a fallback, and must not make the cache look
    "fresh" for a whole TTL and so stop real provider data from arriving.
    """
    last_fetch = db.scalar(select(func.max(PriceBarRow.fetched_at)).where(
        PriceBarRow.symbol == symbol, PriceBarRow.source != CSV_UPLOAD))
    newest_date = db.scalar(select(func.max(PriceBarRow.bar_date)).where(PriceBarRow.symbol == symbol))
    source = None
    if newest_date is not None:
        source = db.scalar(select(PriceBarRow.source).where(
            PriceBarRow.symbol == symbol, PriceBarRow.bar_date == newest_date))
    return last_fetch, newest_date, source


def provider_cooling_down(db: Session, provider: str, endpoint: str, now: datetime,
                          minutes: float | None = None) -> str | None:
    """
    The error kind if this provider's LAST call for this endpoint was a rate limit or an outage
    less than `minutes` ago, else None. While it cools down the provider is not called at all:
    a rate-limited provider is left alone, and an offline demo does not wait through retries on
    every request (it is served the stale cache straight away).
    """
    minutes = settings.STOCK_PROVIDER_COOLDOWN_MINUTES if minutes is None else minutes
    if minutes <= 0:
        return None
    last = db.scalars(
        select(ProviderCall).where(ProviderCall.provider == provider, ProviderCall.endpoint == endpoint)
        .order_by(ProviderCall.at.desc(), ProviderCall.id.desc()).limit(1)
    ).first()
    if last is not None and last.status in COOLDOWN_STATUSES and _utc(now) - _utc(last.at) < timedelta(minutes=minutes):
        return last.status
    return None


def _price_result(db: Session, symbol: str, days: int, today: date, status: CacheStatus,
                  warnings: list[str], provider_error: str | None = None, saved: int = 0) -> PriceResult:
    _, newest_date, source = _price_cache_state(db, symbol)
    bars = _cached_bars(db, symbol, today - timedelta(days=days))
    return PriceResult(symbol, bars, source, newest_date, status, warnings, provider_error, saved)


# --- Public API ---

def get_prices(
    db: Session,
    symbol: str,
    days: int,
    provider: PriceProvider,
    *,
    now: datetime | None = None,
    force_refresh: bool = False,
    ttl_hours: float | None = None,
    history_days: int | None = None,
    cooldown_minutes: float | None = None,
) -> PriceResult:
    """
    Daily bars for the last `days` calendar days, through the fallback chain above.

    `now`, `ttl_hours`, `history_days` and `cooldown_minutes` default to the clock and settings
    (tests pass them). `force_refresh` skips step 1 (the seed script uses it).
    Raises NoMarketDataError only at step 4.
    """
    symbol = normalize_symbol(symbol)
    now = _utc(now or datetime.now(timezone.utc))
    ttl_hours = settings.STOCK_CACHE_TTL_HOURS if ttl_hours is None else ttl_hours
    history_days = settings.STOCK_HISTORY_DAYS if history_days is None else history_days
    if not 1 <= days <= history_days:
        raise ValueError(f"days must be between 1 and {history_days}")
    today = now.astimezone(IST).date()

    # 1. Fresh cache
    last_fetch, newest_date, _ = _price_cache_state(db, symbol)
    if not force_refresh and is_fresh(last_fetch, now, ttl_hours):
        return _price_result(db, symbol, days, today, "fresh", [])

    # 2. Provider (the full window, so the cache always covers any `days`), unless it is cooling down
    try:
        cooling = provider_cooling_down(db, provider.name, "daily_prices", now, cooldown_minutes)
        if cooling:
            raise ProviderError(cooling, "provider cooling down after a recent failure (not called)", provider.name)
        try:
            bars = provider.get_daily_prices(symbol, today - timedelta(days=history_days), today)
            if not bars:            # adapters should raise not_found; do not trust them to
                raise ProviderError("not_found", f"no bars for {symbol}", provider.name)
        except ProviderError as exc:
            _log_call(db, provider.name, "daily_prices", exc.kind, now)    # only REAL calls are logged
            db.commit()
            raise
    except ProviderError as exc:
        logger.warning("prices %s: provider %s failed (%s): %s", symbol, provider.name, exc.kind, exc)
        # 3. Stale cache (any cached rows, CSV uploads included)
        if newest_date is not None:
            return _price_result(db, symbol, days, today, "stale", [DATA_STALE], exc.kind)
        # 4. Nothing at all
        raise NoMarketDataError(symbol, exc.kind) from exc

    saved = upsert_bars(db, bars, provider.name, now)
    _log_call(db, provider.name, "daily_prices", "ok", now)
    db.commit()
    logger.info("prices %s: %d bars from %s", symbol, saved, provider.name)
    return _price_result(db, symbol, days, today, "refreshed", [], saved=saved)


def get_news(
    db: Session,
    symbol: str,
    provider: NewsProvider,
    *,
    limit: int = DEFAULT_NEWS_LIMIT,
    now: datetime | None = None,
    force_refresh: bool = False,
    ttl_hours: float | None = None,
    cooldown_minutes: float | None = None,
) -> NewsResult:
    """Newest `limit` headlines through the same chain; never raises for provider failures."""
    symbol = normalize_symbol(symbol)
    now = _utc(now or datetime.now(timezone.utc))
    ttl_hours = settings.STOCK_CACHE_TTL_HOURS if ttl_hours is None else ttl_hours

    def cached(status: CacheStatus | None, warnings: list[str], error: str | None = None,
               saved: int = 0) -> NewsResult:
        rows = db.scalars(
            select(NewsItemRow).where(NewsItemRow.symbol == symbol)
            .order_by(NewsItemRow.published_at.desc(), NewsItemRow.id.desc()).limit(limit)
        ).all()
        items = [NewsItem(r.symbol, r.headline, r.url, r.publisher, _utc(r.published_at)) for r in rows]
        source = rows[0].source if rows else None
        return NewsResult(symbol, items, source, status if rows else None, warnings, error, saved)

    last_fetch = db.scalar(select(func.max(NewsItemRow.fetched_at)).where(NewsItemRow.symbol == symbol))
    # Headlines are not tied to the market close, so only the TTL decides freshness
    if not force_refresh and last_fetch is not None and now - _utc(last_fetch) < timedelta(hours=ttl_hours):
        return cached("fresh", [])

    try:
        cooling = provider_cooling_down(db, provider.name, "news", now, cooldown_minutes)
        if cooling:
            raise ProviderError(cooling, "provider cooling down after a recent failure (not called)", provider.name)
        try:
            items = provider.get_news(symbol, limit)
        except ProviderError as exc:
            _log_call(db, provider.name, "news", exc.kind, now)
            db.commit()
            raise
    except ProviderError as exc:
        logger.warning("news %s: provider %s failed (%s): %s", symbol, provider.name, exc.kind, exc)
        if last_fetch is not None:
            return cached("stale", [NEWS_STALE], exc.kind)
        return NewsResult(symbol, [], None, None, [NEWS_UNAVAILABLE], exc.kind)

    saved = upsert_news(db, items, provider.name, now)
    _log_call(db, provider.name, "news", "ok", now)
    db.commit()
    return cached("refreshed", [], saved=saved)


def read_prices(db: Session, symbol: str, days: int, *, now: datetime | None = None,
                ttl_hours: float | None = None) -> PriceResult:
    """
    Cache ONLY, never the provider (GET /prices must not trigger outside calls).
    cache_status is "fresh" or "stale" (+ "data_stale"); NoMarketDataError when nothing is cached.
    """
    symbol = normalize_symbol(symbol)
    now = _utc(now or datetime.now(timezone.utc))
    ttl_hours = settings.STOCK_CACHE_TTL_HOURS if ttl_hours is None else ttl_hours
    if days < 1:
        raise ValueError("days must be at least 1")
    last_fetch, newest_date, _ = _price_cache_state(db, symbol)
    if newest_date is None:
        raise NoMarketDataError(symbol, "not_cached")
    today = now.astimezone(IST).date()
    if is_fresh(last_fetch, now, ttl_hours):
        return _price_result(db, symbol, days, today, "fresh", [])
    return _price_result(db, symbol, days, today, "stale", [DATA_STALE])
