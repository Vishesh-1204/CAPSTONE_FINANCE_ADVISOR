# app/market_data/base.py
#
# Purpose: the provider-neutral contract for market data (Phase 7, spec section 8). Same idea
# as llm/base.py: the rest of the app only knows these dataclasses and protocols, never a
# vendor library. Switching provider is one environment variable (see factory.py).
#
#   PriceBar / NewsItem   what every adapter returns (frozen dataclasses, exact Decimal prices)
#   PriceProvider         get_daily_prices(symbol, start, end)
#   NewsProvider          get_news(symbol, limit)
#   MarketDataProvider    both (the spec's single protocol; yfinance implements it)
#   ProviderError         the ONE error adapters raise, with a `kind` the cache service acts on
#   call_with_retries     the shared polite-retry rule: at most 2 retries, backoff, and only
#                         for temporary failures (never for a rate limit)
#
# Prices and news are split into two protocols because the spike chose different sources for
# them (yfinance prices, Google News RSS headlines); an RSS feed has no prices to offer.

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as clock_time
from decimal import Decimal
from typing import Callable, Literal, Protocol, TypeVar, runtime_checkable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Indian market clock. NSE closes at 15:30 IST and providers publish the final bar a little
# later, so a bar dated today only counts as a real close from 16:00 IST.
IST = ZoneInfo("Asia/Kolkata")
CLOSE_FINAL_AFTER_IST = clock_time(16, 0)

# Every outside request has a timeout (seconds); nothing may hang the API.
REQUEST_TIMEOUT_SECONDS = 20.0
# Temporary failures are retried at most this many times, waiting BACKOFF_SECONDS[i] before retry i+1.
MAX_RETRIES = 2
BACKOFF_SECONDS: tuple[float, ...] = (2.0, 6.0)

ErrorKind = Literal["rate_limited", "unavailable", "not_found", "bad_response"]
ERROR_KINDS: tuple[str, ...] = ("rate_limited", "unavailable", "not_found", "bad_response")


@dataclass(frozen=True)
class PriceBar:
    """One daily bar as an adapter returns it (not the database row; see models/stock.py)."""
    symbol: str
    bar_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None


@dataclass(frozen=True)
class NewsItem:
    """One headline: text, link, publisher and time. Never the article body."""
    symbol: str
    headline: str
    url: str
    publisher: str | None
    published_at: datetime        # timezone-aware (UTC)


class ProviderError(Exception):
    """
    Raised by every adapter for every failure, so the cache service has one thing to catch.

    kind:
      rate_limited  the provider said "too many requests"  -> never retried, use stale cache
      unavailable   network error, timeout, 5xx             -> retried (max 2), then stale cache
      not_found     unknown symbol / no data for it          -> not retried
      bad_response  the reply could not be understood       -> not retried
    """

    def __init__(self, kind: ErrorKind, message: str, provider: str = "") -> None:
        if kind not in ERROR_KINDS:
            raise ValueError(f"unknown ProviderError kind: {kind}")
        super().__init__(message)
        self.kind: ErrorKind = kind
        self.provider = provider


@runtime_checkable
class PriceProvider(Protocol):
    name: str

    def get_daily_prices(self, symbol: str, start: date, end: date) -> list[PriceBar]:
        """Bars with start <= bar_date <= end, oldest first. Raises ProviderError."""
        ...


@runtime_checkable
class NewsProvider(Protocol):
    name: str

    def get_news(self, symbol: str, limit: int) -> list[NewsItem]:
        """At most `limit` headlines, newest first. [] is a valid answer. Raises ProviderError."""
        ...


@runtime_checkable
class MarketDataProvider(PriceProvider, NewsProvider, Protocol):
    """The spec's single protocol: a provider that offers both prices and news."""


T = TypeVar("T")


def call_with_retries(
    fn: Callable[[], T],
    *,
    label: str,
    retries: int = MAX_RETRIES,
    backoff: tuple[float, ...] = BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """
    Run fn(); on ProviderError(kind="unavailable") wait and try again, at most `retries` times.

    Every other kind is raised at once: retrying a rate limit only burns quota, and a missing
    symbol or a broken reply will not fix itself. `sleep` is injectable so tests never wait.
    """
    for attempt in range(retries + 1):
        try:
            return fn()
        except ProviderError as exc:
            if exc.kind != "unavailable" or attempt == retries:
                raise
            wait = backoff[min(attempt, len(backoff) - 1)]
            logger.info("%s: temporary failure (%s), retry %d/%d in %.0fs", label, exc, attempt + 1, retries, wait)
            sleep(wait)
    raise AssertionError("unreachable")  # the loop always returns or raises
