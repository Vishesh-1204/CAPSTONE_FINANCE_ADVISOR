# tests/test_market_cache.py
#
# Purpose: Phase 7A-3 cache tests. cache_service must follow the fallback chain EXACTLY
# (fresh cache -> provider -> stale cache + "data_stale" -> clear error pointing to CSV upload),
# upsert without duplicates, and log every provider call in provider_calls.
# FakeProvider (tests/market_fakes.py) stands in for yfinance / RSS; the bars are a SYNTHETIC
# seeded random walk made by code (no real market data in the repo). No network (block_network), in-memory SQLite.

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.market_data import cache_service as cache
from app.market_data.base import NewsItem, PriceBar, ProviderError
from app.models.stock import NewsItemRow, PriceBar as PriceBarRow, ProviderCall
from tests.market_fakes import FakeProvider, synthetic_bars

pytestmark = pytest.mark.usefixtures("block_network")

SYMBOL = "SYNTH.NS"
# Monday 2026-11-09 17:30 IST: after the close, so the cutoff is today 16:00 IST (10:30 UTC)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 9)
TTL = 24.0
HISTORY = 400


def prices(db, provider, days=30, now=NOW, **kwargs):
    return cache.get_prices(db, SYMBOL, days, provider, now=now, ttl_hours=TTL, history_days=HISTORY, **kwargs)


def seed_cache(db, fetched_at: datetime, bars: list[PriceBar] | None = None, source: str = "fake") -> None:
    cache.upsert_bars(db, bars or synthetic_bars(), source, fetched_at)
    db.commit()


def logged_calls(db) -> list[tuple[str, str, str]]:
    return [(c.provider, c.endpoint, c.status) for c in db.query(ProviderCall).order_by(ProviderCall.id)]


# --- Step 2 then step 1: miss -> fetch -> fresh hit ---

def test_miss_then_fetch_then_fresh_hit(db):
    provider = FakeProvider()
    first = prices(db, provider)
    assert first.cache_status == "refreshed" and first.warnings == []
    assert len(provider.price_calls) == 1
    # The FULL history window is fetched, whatever `days` asked for
    assert provider.price_calls[0] == (SYMBOL, TODAY - timedelta(days=HISTORY), TODAY)
    assert db.query(PriceBarRow).count() == len(synthetic_bars())
    assert first.bars and all(b.bar_date >= TODAY - timedelta(days=30) for b in first.bars)
    assert first.data_as_of == TODAY and first.source == "fake"
    assert logged_calls(db) == [("fake", "daily_prices", "ok")]

    second = prices(db, provider, days=365, now=NOW + timedelta(hours=1))
    assert second.cache_status == "fresh" and len(provider.price_calls) == 1     # no second request
    assert len(second.bars) > len(first.bars)                                     # served from the cache
    assert len(logged_calls(db)) == 1                                             # cache hits are not provider calls


def test_fresh_cache_never_calls_the_provider(db):
    seed_cache(db, NOW - timedelta(hours=1))
    provider = FakeProvider(error=ProviderError("unavailable", "must not be called"))
    result = prices(db, provider)
    assert result.cache_status == "fresh" and result.warnings == [] and provider.price_calls == []


def test_cache_older_than_the_ttl_is_refetched(db):
    seed_cache(db, NOW - timedelta(hours=TTL + 1))
    provider = FakeProvider()
    assert prices(db, provider).cache_status == "refreshed" and len(provider.price_calls) == 1


def test_a_morning_cache_is_not_fresh_after_the_close(db):
    """Filled at 09:00 IST (before the close) and read at 17:30 IST: inside the TTL but missing today's close."""
    seed_cache(db, datetime(2026, 11, 9, 3, 30, tzinfo=timezone.utc))
    provider = FakeProvider()
    assert prices(db, provider).cache_status == "refreshed"


def test_force_refresh_skips_the_fresh_cache(db):
    seed_cache(db, NOW - timedelta(minutes=5))
    provider = FakeProvider()
    assert prices(db, provider, force_refresh=True).cache_status == "refreshed"
    assert len(provider.price_calls) == 1


@pytest.mark.parametrize("fetched, now, expected", [
    # Friday 17:00 IST fetch, read on Sunday noon with a 72 h TTL: still fresh (no close since Friday)
    (datetime(2026, 11, 6, 11, 30, tzinfo=timezone.utc), datetime(2026, 11, 8, 6, 30, tzinfo=timezone.utc), True),
    # Friday 17:00 IST fetch, read Monday 17:30 IST: Monday's close happened -> not fresh
    (datetime(2026, 11, 6, 11, 30, tzinfo=timezone.utc), datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc), False),
    # Monday 10:00 IST fetch, read Monday 15:00 IST: before the close -> fresh
    (datetime(2026, 11, 9, 4, 30, tzinfo=timezone.utc), datetime(2026, 11, 9, 9, 30, tzinfo=timezone.utc), True),
    # Naive datetimes (what SQLite returns) are read as UTC
    (datetime(2026, 11, 9, 11, 0), datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc), True),
])
def test_freshness_rule(fetched, now, expected):
    assert cache.is_fresh(fetched, now, ttl_hours=72) is expected


# --- Step 3: provider fails, stale cache with the warning ---

def test_rate_limited_then_stale_cache_with_the_data_stale_warning(db):
    seed_cache(db, NOW - timedelta(hours=30), synthetic_bars(end=TODAY - timedelta(days=1)))
    provider = FakeProvider(error=ProviderError("rate_limited", "429"))
    result = prices(db, provider)
    assert result.cache_status == "stale"
    assert result.warnings == [cache.DATA_STALE] and result.provider_error == "rate_limited"
    assert result.bars and result.data_as_of == date(2026, 11, 6)        # newest cached bar (Friday)
    assert len(provider.price_calls) == 1
    assert logged_calls(db) == [("fake", "daily_prices", "rate_limited")]


@pytest.mark.parametrize("kind", ["unavailable", "not_found", "bad_response"])
def test_every_failure_kind_falls_back_to_stale_cache(db, kind):
    seed_cache(db, NOW - timedelta(hours=30))
    result = prices(db, FakeProvider(error=ProviderError(kind, "x")))
    assert result.cache_status == "stale" and result.warnings == ["data_stale"] and result.provider_error == kind


# --- Step 4: nothing cached ---

def test_nothing_cached_and_provider_down_gives_a_clear_error(db):
    with pytest.raises(cache.NoMarketDataError) as info:
        prices(db, FakeProvider(error=ProviderError("unavailable", "timeout")))
    message = str(info.value)
    assert SYMBOL in message and "unavailable" in message
    assert f"POST /api/stocks/{SYMBOL}/prices/upload" in message and "CSV" in message
    assert info.value.kind == "unavailable"
    # The failed call is still logged (committed before raising)
    assert logged_calls(db) == [("fake", "daily_prices", "unavailable")]


# --- Upsert ---

def test_upsert_does_not_duplicate_and_keeps_the_newest_values(db):
    provider = FakeProvider()
    prices(db, provider)
    count = db.query(PriceBarRow).count()

    provider.bars = synthetic_bars(bump=Decimal("5"))      # same days, revised prices
    prices(db, provider, force_refresh=True, now=NOW + timedelta(minutes=10))
    assert db.query(PriceBarRow).count() == count          # no duplicates
    newest = db.query(PriceBarRow).filter_by(bar_date=TODAY).one()
    assert newest.close == synthetic_bars()[-1].close + Decimal("5")
    assert logged_calls(db) == [("fake", "daily_prices", "ok")] * 2


def test_csv_upload_rows_are_overwritten_by_the_provider_for_the_same_day(db):
    seed_cache(db, NOW - timedelta(hours=30), synthetic_bars(days=10), source="csv_upload")
    assert prices(db, FakeProvider(error=ProviderError("rate_limited", "x"))).source == "csv_upload"
    result = prices(db, FakeProvider(), now=NOW + timedelta(hours=1))      # after the cooldown
    assert result.source == "fake" and db.query(PriceBarRow).filter_by(source="csv_upload").count() == 0


def test_a_csv_upload_never_overwrites_cached_bars_and_only_adds_missing_days(db):
    provider_bars = synthetic_bars(days=10)
    seed_cache(db, NOW - timedelta(hours=1), provider_bars)
    upload = synthetic_bars(days=20, bump=Decimal("50"))                     # overlaps, with other prices
    cache.upsert_bars(db, upload, cache.CSV_UPLOAD, NOW, overwrite=False)
    db.commit()
    kept = db.query(PriceBarRow).filter_by(bar_date=provider_bars[-1].bar_date).one()
    assert kept.source == "fake" and kept.close == provider_bars[-1].close   # shared data not changed
    added = db.query(PriceBarRow).filter_by(source=cache.CSV_UPLOAD).count()
    assert added == len(upload) - len(provider_bars) > 0                     # only the missing days


def test_csv_rows_do_not_make_the_cache_look_fresh(db):
    """A fresh upload is a fallback: the provider is still asked for real data."""
    seed_cache(db, NOW - timedelta(minutes=5), synthetic_bars(days=10), source="csv_upload")
    provider = FakeProvider()
    assert prices(db, provider).cache_status == "refreshed" and len(provider.price_calls) == 1


# --- Provider cooldown ---

@pytest.mark.parametrize("kind", ["rate_limited", "unavailable"])
def test_after_a_rate_limit_or_outage_the_provider_is_left_alone(db, kind):
    seed_cache(db, NOW - timedelta(hours=30))
    prices(db, FakeProvider(error=ProviderError(kind, "x")))
    later = FakeProvider()
    result = prices(db, later, now=NOW + timedelta(minutes=5))
    assert later.price_calls == [] and result.cache_status == "stale" and result.provider_error == kind
    assert len(logged_calls(db)) == 1                     # skipped calls are not logged as calls
    after = prices(db, later, now=NOW + timedelta(minutes=20))
    assert after.cache_status == "refreshed" and len(later.price_calls) == 1


def test_not_found_does_not_cool_the_provider_down(db):
    """A missing symbol says nothing about the provider: other symbols are still fetched."""
    prices_for = lambda symbol, provider: cache.get_prices(db, symbol, 30, provider, now=NOW, ttl_hours=TTL,
                                                           history_days=HISTORY)
    with pytest.raises(cache.NoMarketDataError):
        prices_for("NOPE.NS", FakeProvider(error=ProviderError("not_found", "x")))
    provider = FakeProvider()
    assert prices_for(SYMBOL, provider).cache_status == "refreshed"


def test_cooldown_can_be_switched_off(db):
    seed_cache(db, NOW - timedelta(hours=30))
    prices(db, FakeProvider(error=ProviderError("rate_limited", "x")))
    provider = FakeProvider()
    assert prices(db, provider, cooldown_minutes=0).cache_status == "refreshed"


def test_news_cooldown(db):
    news(db, FakeProvider(error=ProviderError("rate_limited", "x")))
    later = FakeProvider(news=_headlines())
    assert news(db, later, now=NOW + timedelta(minutes=5)).warnings == [cache.NEWS_UNAVAILABLE]
    assert later.news_calls == []


# --- Cache-only read (GET /prices) ---

def test_read_prices_never_calls_a_provider_and_marks_old_data_stale(db):
    with pytest.raises(cache.NoMarketDataError, match="refresh it first"):
        cache.read_prices(db, SYMBOL, 30, now=NOW)
    seed_cache(db, NOW - timedelta(hours=1))
    fresh = cache.read_prices(db, SYMBOL, 30, now=NOW, ttl_hours=TTL)
    assert fresh.cache_status == "fresh" and fresh.warnings == [] and fresh.bars
    old = cache.read_prices(db, SYMBOL, 30, now=NOW + timedelta(days=3), ttl_hours=TTL)
    assert old.cache_status == "stale" and old.warnings == [cache.DATA_STALE]


def test_prices_are_stored_exactly(db):
    bar = PriceBar(SYMBOL, TODAY, Decimal("2950.1234"), Decimal("2960.0000"), Decimal("2940.5000"),
                   Decimal("2955.9999"), None)
    result = prices(db, FakeProvider(bars=[bar]), days=1)
    assert result.bars == [bar]


# --- Input checks ---

@pytest.mark.parametrize("days", [0, HISTORY + 1])
def test_days_must_be_inside_the_history_window(db, days):
    with pytest.raises(ValueError):
        prices(db, FakeProvider(), days=days)


def test_bad_symbol_is_rejected_before_the_provider(db):
    provider = FakeProvider()
    with pytest.raises(ValueError):
        cache.get_prices(db, "../etc", 30, provider, now=NOW)
    assert provider.price_calls == []


# --- News: same chain, but "nothing" is a warning, not an error ---

def _headlines(n: int = 3, start: datetime = NOW) -> list[NewsItem]:
    return [NewsItem(SYMBOL, f"Synthetic headline {i}", f"https://example.com/n/{i}", "Example Wire",
                     start - timedelta(hours=i)) for i in range(n)]


def news(db, provider, now=NOW, **kwargs):
    return cache.get_news(db, SYMBOL, provider, limit=10, now=now, ttl_hours=TTL, **kwargs)


def test_news_miss_then_fetch_then_fresh_hit(db):
    provider = FakeProvider(news=_headlines())
    first = news(db, provider)
    assert first.cache_status == "refreshed" and [i.headline for i in first.items][0] == "Synthetic headline 0"
    second = news(db, provider, now=NOW + timedelta(hours=2))
    assert second.cache_status == "fresh" and len(provider.news_calls) == 1 and len(second.items) == 3
    assert logged_calls(db) == [("fake", "news", "ok")]


def test_news_provider_failure_uses_stale_headlines(db):
    news(db, FakeProvider(news=_headlines()))
    later = NOW + timedelta(hours=TTL + 1)
    result = news(db, FakeProvider(error=ProviderError("rate_limited", "429")), now=later)
    assert result.cache_status == "stale" and result.warnings == [cache.NEWS_STALE] and len(result.items) == 3


def test_no_news_at_all_is_a_warning_not_an_error(db):
    result = news(db, FakeProvider(error=ProviderError("unavailable", "down")))
    assert result.items == [] and result.cache_status is None
    assert result.warnings == [cache.NEWS_UNAVAILABLE] and result.provider_error == "unavailable"
    assert logged_calls(db) == [("fake", "news", "unavailable")]


def test_news_upsert_keeps_existing_sentiment_and_does_not_duplicate(db):
    news(db, FakeProvider(news=_headlines()))
    row = db.query(NewsItemRow).filter_by(url="https://example.com/n/0").one()
    row.sentiment_positive, row.sentiment_negative, row.sentiment_neutral, row.scorer_name = 0.7, 0.1, 0.2, "lexicon"
    db.commit()

    news(db, FakeProvider(news=_headlines()), force_refresh=True)
    assert db.query(NewsItemRow).count() == 3
    row = db.query(NewsItemRow).filter_by(url="https://example.com/n/0").one()
    db.refresh(row)
    assert (row.sentiment_positive, row.scorer_name) == (0.7, "lexicon")
