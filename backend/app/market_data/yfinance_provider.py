# app/market_data/yfinance_provider.py
#
# Purpose: MarketDataProvider adapter for yfinance (the unofficial Yahoo Finance route).
# Chosen by the 7A-0 spike (docs/phase7-provider-spike.md): the only free source with 252+
# daily bars for NSE symbols (.NS) and the Nifty 50 index (^NSEI).
#
# This is the ONLY file in the app that may import yfinance (approved 2026-11-09 for the
# data-provider adapter only), and it imports it lazily, inside the default fetchers, so the
# app and the tests start without touching it. Tests inject fake fetchers and never use the network.
#
# Data choices (also in docs):
#   * auto_adjust=False: Open/High/Low/Close are Yahoo's split-adjusted prices, NOT adjusted
#     for dividends. So returns here are PRICE returns (dividends excluded) and match the
#     prices people see on charts.
#   * Today's bar is dropped until 16:00 IST: before the close it is an unfinished session
#     (the spike caught this), and caching it would store a fake "close".
#   * Rows with a missing or non-positive price, or low above high, are dropped, never repaired.
#
# Errors become ProviderError: rate limit -> rate_limited (never retried), unknown symbol or no
# rows -> not_found, malformed data -> bad_response, anything else (network, timeout) -> unavailable
# (at most 2 retries with backoff). yfinance exceptions are matched by CLASS NAME so this
# mapping is testable without importing yfinance.

import logging
import math
import time
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable

from app.core.stock_symbols import normalize_symbol
from app.market_data.base import (
    CLOSE_FINAL_AFTER_IST, IST, REQUEST_TIMEOUT_SECONDS, NewsItem, PriceBar, ProviderError, call_with_retries,
)

logger = logging.getLogger(__name__)

PRICE_STEP = Decimal("0.0001")      # price_bars columns are Numeric(14,4)
MAX_HEADLINE_LENGTH = 500
MAX_URL_LENGTH = 2048

# yfinance exception class names -> our error kinds
_RATE_LIMIT_NAMES = {"YFRateLimitError"}
_NOT_FOUND_NAMES = {"YFTickerMissingError", "YFPricesMissingError", "YFTzMissingError", "YFInvalidPeriodError"}
_BAD_RESPONSE_NAMES = {"YFDataException"}

HistoryFetch = Callable[[str, date, date, float], Any]     # returns a pandas DataFrame
NewsFetch = Callable[[str, float], list]


# Words yfinance writes to its log when the NETWORK failed (it often logs instead of raising)
_NETWORK_LOG_MARKERS = ("could not resolve", "failed to perform", "dnserror", "timed out", "timeout",
                        "connection", "network is unreachable")


class _NetworkTroubleHandler(logging.Handler):
    """Listens to yfinance's logger during one call and remembers whether it reported a network failure."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.seen = False

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage().lower()
        self.seen |= any(marker in message for marker in _NETWORK_LOG_MARKERS)


def classify_error(exc: Exception) -> str:
    """Map any exception raised while talking to Yahoo to a ProviderError kind."""
    name = type(exc).__name__
    if name in _RATE_LIMIT_NAMES or "too many requests" in str(exc).lower():
        return "rate_limited"
    if name in _NOT_FOUND_NAMES:
        return "not_found"
    if name in _BAD_RESPONSE_NAMES:
        return "bad_response"
    return "unavailable"          # network errors, timeouts, anything unexpected: temporary


def _default_history_fetch(symbol: str, start: date, end: date, timeout: float):
    import yfinance as yf          # lazy: only when a real request is made
    # `end` is exclusive in yfinance, so ask for one day more
    return yf.Ticker(symbol).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                     interval="1d", auto_adjust=False, actions=False, timeout=timeout)


def _default_news_fetch(symbol: str, timeout: float) -> list:
    import yfinance as yf
    return list(yf.Ticker(symbol).news or [])


def _price(value: Any) -> Decimal | None:
    """A finite, positive price rounded to 4 decimals, else None. str() avoids float noise."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return Decimal(str(number)).quantize(PRICE_STEP, rounding=ROUND_HALF_UP)   # not banker's rounding


def _volume(value: Any) -> int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if math.isfinite(number) and number >= 0 else None


def frame_to_bars(symbol: str, frame: Any, start: date, end: date, now: datetime) -> list[PriceBar]:
    """
    yfinance DataFrame (index = exchange-local timestamps; Open/High/Low/Close/Volume columns)
    -> clean PriceBars, oldest first. Pure apart from reading the frame; tests use fixtures.
    """
    now_ist = now.astimezone(IST)
    today_is_final = now_ist.time() >= CLOSE_FINAL_AFTER_IST
    bars: dict[date, PriceBar] = {}
    dropped = 0
    for timestamp, row in frame.iterrows():
        bar_date = timestamp.date() if hasattr(timestamp, "date") else date.fromisoformat(str(timestamp)[:10])
        if not start <= bar_date <= end or bar_date > now_ist.date():
            continue
        if bar_date == now_ist.date() and not today_is_final:
            continue                                  # unfinished session: not a real close
        o, h, l, c = (_price(row.get(column)) for column in ("Open", "High", "Low", "Close"))
        if None in (o, h, l, c) or l > h:
            dropped += 1
            continue
        bars[bar_date] = PriceBar(symbol, bar_date, o, h, l, c, _volume(row.get("Volume")))
    if dropped:
        logger.info("yfinance %s: dropped %d invalid rows", symbol, dropped)
    return [bars[d] for d in sorted(bars)]


def _news_fields(item: dict) -> tuple[str, str, str | None, datetime | None]:
    """(title, url, publisher, published_at) from either yfinance news shape (old flat, new 'content')."""
    if "content" in item and isinstance(item["content"], dict):
        content = item["content"]
        url = ((content.get("canonicalUrl") or {}).get("url")
               or (content.get("clickThroughUrl") or {}).get("url") or "")
        publisher = (content.get("provider") or {}).get("displayName")
        published = None
        stamp = content.get("pubDate") or content.get("displayTime")
        if stamp:
            try:
                published = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except ValueError:
                published = None
        return str(content.get("title") or ""), str(url), publisher, published
    published = None
    if item.get("providerPublishTime") is not None:
        try:
            published = datetime.fromtimestamp(int(item["providerPublishTime"]), tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            published = None
    return str(item.get("title") or ""), str(item.get("link") or ""), item.get("publisher"), published


def raw_news_to_items(symbol: str, raw: list) -> list[NewsItem]:
    """yfinance news list -> NewsItems, newest first, one per link; incomplete items are skipped."""
    items: dict[str, NewsItem] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        title, url, publisher, published = _news_fields(entry)
        title = " ".join(title.split())
        url = url.strip()
        if not title or not url.startswith(("https://", "http://")) or len(url) > MAX_URL_LENGTH or not published:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        publisher = (" ".join(str(publisher).split()) or None) if publisher else None
        items.setdefault(url, NewsItem(symbol, title[:MAX_HEADLINE_LENGTH], url, publisher,
                                       published.astimezone(timezone.utc)))
    return sorted(items.values(), key=lambda item: item.published_at, reverse=True)


class YFinanceProvider:
    """Prices (and, if NEWS_PROVIDER=yfinance, headlines) from yfinance."""

    name = "yfinance"

    def __init__(self, history_fetch: HistoryFetch | None = None, news_fetch: NewsFetch | None = None,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = REQUEST_TIMEOUT_SECONDS,
                 clock: Callable[[], datetime] | None = None) -> None:
        self._history_fetch = history_fetch or _default_history_fetch
        self._news_fetch = news_fetch or _default_news_fetch
        self._sleep = sleep
        self._timeout = timeout
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _call(self, fn: Callable[[], Any], label: str) -> tuple[Any, bool]:
        """
        Run one yfinance call, turning its exceptions into ProviderError (with retries).
        Returns (result, network_trouble): network_trouble is True when ANY attempt hit a network
        error or yfinance LOGGED one. Needed because offline, the first attempt raises DNSError
        but the retry quietly returns an EMPTY table, which must not be reported as "not_found".
        """
        trouble = _NetworkTroubleHandler()
        yf_logger = logging.getLogger("yfinance")
        yf_logger.addHandler(trouble)

        def attempt() -> Any:
            try:
                return fn()
            except ProviderError:
                raise
            except Exception as exc:  # noqa: BLE001 - yfinance raises many unrelated types
                kind = classify_error(exc)
                trouble.seen |= kind == "unavailable"
                raise ProviderError(kind, f"{type(exc).__name__}: {exc}", self.name) from exc
        try:
            return call_with_retries(attempt, label=label, sleep=self._sleep), trouble.seen
        finally:
            yf_logger.removeHandler(trouble)

    def get_daily_prices(self, symbol: str, start: date, end: date) -> list[PriceBar]:
        symbol = normalize_symbol(symbol)
        if start > end:
            raise ValueError("start must not be after end")
        frame, network_trouble = self._call(lambda: self._history_fetch(symbol, start, end, self._timeout),
                                            f"yfinance {symbol}")
        if frame is None or not hasattr(frame, "iterrows"):
            raise ProviderError("bad_response", "history() did not return a table", self.name)
        if frame.empty:
            if network_trouble:      # an empty table after a network error is an outage, not a missing symbol
                raise ProviderError("unavailable", f"no data for {symbol} after a network error", self.name)
            raise ProviderError("not_found", f"no price data for {symbol}", self.name)
        try:
            bars = frame_to_bars(symbol, frame, start, end, self._clock())
        except (AttributeError, KeyError, TypeError) as exc:
            raise ProviderError("bad_response", f"unexpected table shape: {exc}", self.name) from exc
        if not bars:
            raise ProviderError("not_found", f"no valid bars for {symbol} in the range", self.name)
        return bars

    def get_news(self, symbol: str, limit: int) -> list[NewsItem]:
        symbol = normalize_symbol(symbol)
        if limit < 1:
            raise ValueError("limit must be at least 1")
        raw, _ = self._call(lambda: self._news_fetch(symbol, self._timeout), f"yfinance news {symbol}")
        if not isinstance(raw, list):
            raise ProviderError("bad_response", "news is not a list", self.name)
        return raw_news_to_items(symbol, raw)[:limit]
