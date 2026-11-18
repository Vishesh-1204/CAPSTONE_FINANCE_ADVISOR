# tests/market_fakes.py
#
# Purpose: shared fakes for the Phase 7 tests (cache, service, API). A FakeProvider stands in
# for yfinance / Google News, and the bars are a SYNTHETIC seeded random walk made by code, so
# no real market data is in the repo and no test touches the network.

import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.market_data.base import NewsItem, PriceBar, ProviderError

SYMBOL = "SYNTH.NS"
BENCHMARK = "^NSEI"
# Monday 2026-11-09 17:30 IST: after the close, so the freshness cutoff is today 16:00 IST
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 9)


def synthetic_bars(symbol: str = SYMBOL, end: date = TODAY, days: int = 400, seed: int = 42,
                   bump: Decimal = Decimal("0"), drift: float = 0.0004, volatility: float = 0.015) -> list[PriceBar]:
    """A seeded random walk on weekdays: reproducible, made up, no real prices."""
    rng = random.Random(seed)
    price, bars = 100.0, []
    day = end - timedelta(days=days)
    while day <= end:
        if day.weekday() < 5:
            price *= 1 + rng.gauss(drift, volatility)
            close = Decimal(str(round(price, 4))) + bump
            bars.append(PriceBar(symbol, day, close, close + Decimal("1"), close - Decimal("1"), close, 1000))
        day += timedelta(days=1)
    return bars


def headlines(n: int = 3, symbol: str = SYMBOL, start: datetime = NOW) -> list[NewsItem]:
    """Synthetic headlines, newest first: alternating good and bad news words for the lexicon."""
    words = ["profit rises", "shares plunge", "board meets"]
    return [NewsItem(symbol, f"Synthetic Corp {words[i % 3]} {i}", f"https://example.com/n/{symbol}/{i}",
                     "Example Wire", start - timedelta(hours=i)) for i in range(n)]


class FakeProvider:
    """Records every call; returns bars/news inside the requested range, or raises `error`.

    `name` defaults to "fake"; the API tests use the real names ("yfinance", "google_rss")
    because the response schemas only accept known sources."""

    def __init__(self, bars: list[PriceBar] | None = None, news: list[NewsItem] | None = None,
                 error: ProviderError | None = None, name: str = "fake") -> None:
        self.name = name
        self.bars = bars if bars is not None else synthetic_bars()
        self.news = news or []
        self.error = error
        self.price_calls: list[tuple] = []
        self.news_calls: list[tuple] = []

    def get_daily_prices(self, symbol, start, end):
        self.price_calls.append((symbol, start, end))
        if self.error:
            raise self.error
        return [b for b in self.bars if b.symbol == symbol and start <= b.bar_date <= end]

    def get_news(self, symbol, limit):
        self.news_calls.append((symbol, limit))
        if self.error:
            raise self.error
        return [item for item in self.news if item.symbol == symbol][:limit]
