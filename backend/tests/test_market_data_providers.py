# tests/test_market_data_providers.py
#
# Purpose: Phase 7A-3 adapter tests. The yfinance and Google News RSS adapters are fed small
# SYNTHETIC fixtures in the shape of real responses (tests/fixtures/market_data/) through
# injected fetchers, so NOTHING here touches the network (block_network makes sure).
# Covers: response -> dataclass conversion, dropping bad rows and today's unfinished bar,
# exact Decimal prices, error -> ProviderError(kind) mapping, the "at most 2 retries, never
# for a rate limit" rule, URL safety, the factory, and the vendor-import boundary.

import json
import urllib.error
from datetime import date, datetime, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest

from app.market_data import rss_news_provider as rss
from app.market_data.base import (
    ERROR_KINDS, MAX_RETRIES, MarketDataProvider, NewsProvider, PriceProvider, ProviderError, call_with_retries,
)
from app.market_data.factory import get_news_provider, get_price_provider
from app.market_data.rss_news_provider import GoogleNewsRssProvider, news_query, parse_feed
from app.market_data.yfinance_provider import YFinanceProvider, classify_error, frame_to_bars, raw_news_to_items
from app.models.stock import PROVIDER_CALL_STATUSES

pytestmark = pytest.mark.usefixtures("block_network")

FIXTURES = Path(__file__).parent / "fixtures" / "market_data"
APP_DIR = Path(__file__).resolve().parent.parent / "app"
# Monday 2026-11-09: 17:30 IST (after the close) and 10:30 IST (market open, bar unfinished)
AFTER_CLOSE = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
DURING_SESSION = datetime(2026, 11, 9, 5, 0, tzinfo=timezone.utc)
START, END = date(2026, 10, 1), date(2026, 11, 9)


def history_frame() -> pd.DataFrame:
    """The yfinance fixture as the DataFrame yfinance would return (exchange-local index)."""
    data = json.loads((FIXTURES / "yfinance_history_sample.json").read_text(encoding="utf-8"))
    frame = pd.DataFrame(data["rows"])
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("Date"))).tz_localize(data["timezone"])
    return frame


def news_fixture() -> list:
    return json.loads((FIXTURES / "yfinance_news_sample.json").read_text(encoding="utf-8"))["items"]


def rss_fixture() -> bytes:
    return (FIXTURES / "google_news_rss_sample.xml").read_bytes()


class Recorder:
    """A fetcher stand-in: returns or raises the queued outcomes in order and records each call."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _named_error(name: str, message: str = "boom") -> Exception:
    """An exception whose CLASS NAME matches a yfinance exception (no yfinance import needed)."""
    return type(name, (Exception,), {})(message)


# --- Contract ---

def test_error_kinds_plus_ok_are_exactly_the_logged_statuses():
    assert ("ok", *ERROR_KINDS) == PROVIDER_CALL_STATUSES


def test_provider_error_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        ProviderError("broken", "x")


def test_adapters_satisfy_the_protocols():
    assert isinstance(YFinanceProvider(), MarketDataProvider)
    assert isinstance(GoogleNewsRssProvider(), NewsProvider)
    assert not isinstance(GoogleNewsRssProvider(), PriceProvider)     # an RSS feed has no prices


# --- Retry rule ---

def test_temporary_failures_are_retried_at_most_twice_with_backoff():
    sleeps: list[float] = []
    fn = Recorder(ProviderError("unavailable", "down"))
    with pytest.raises(ProviderError) as info:
        call_with_retries(fn, label="t", sleep=sleeps.append)
    assert info.value.kind == "unavailable"
    assert len(fn.calls) == 1 + MAX_RETRIES and sleeps == [2.0, 6.0]


def test_a_retry_that_succeeds_returns_the_value():
    sleeps: list[float] = []
    fn = Recorder(ProviderError("unavailable", "blip"), "ok")
    assert call_with_retries(fn, label="t", sleep=sleeps.append) == "ok"
    assert len(fn.calls) == 2 and sleeps == [2.0]


@pytest.mark.parametrize("kind", ["rate_limited", "not_found", "bad_response"])
def test_permanent_failures_and_rate_limits_are_never_retried(kind):
    sleeps: list[float] = []
    fn = Recorder(ProviderError(kind, "no"))
    with pytest.raises(ProviderError):
        call_with_retries(fn, label="t", sleep=sleeps.append)
    assert len(fn.calls) == 1 and sleeps == []


# --- yfinance prices ---

def test_frame_conversion_drops_bad_rows_and_rounds_to_four_decimals():
    bars = frame_to_bars("SYNTH.NS", history_frame(), START, END, AFTER_CLOSE)
    assert [b.bar_date for b in bars] == [date(2026, 11, d) for d in (2, 3, 4, 5, 6, 9)]
    first = bars[0]
    assert first.close == Decimal("101.1235") and first.open == Decimal("100.0000")
    assert first.volume == 1_500_000 and bars[3].volume is None          # missing volume -> None
    assert all(isinstance(b.close, Decimal) for b in bars)


def test_todays_unfinished_bar_is_dropped_before_the_close():
    bars = frame_to_bars("SYNTH.NS", history_frame(), START, END, DURING_SESSION)
    assert bars[-1].bar_date == date(2026, 11, 6)


def test_frame_conversion_respects_the_requested_range():
    bars = frame_to_bars("SYNTH.NS", history_frame(), date(2026, 11, 3), date(2026, 11, 4), AFTER_CLOSE)
    assert [b.bar_date for b in bars] == [date(2026, 11, 3), date(2026, 11, 4)]


def test_yfinance_provider_returns_clean_bars_through_the_injected_fetcher():
    fetch = Recorder(history_frame())
    provider = YFinanceProvider(history_fetch=fetch, clock=lambda: AFTER_CLOSE)
    bars = provider.get_daily_prices("synth.ns", START, END)
    assert len(bars) == 6 and bars[0].symbol == "SYNTH.NS"
    symbol, start, end, timeout = fetch.calls[0]
    assert (symbol, start, end) == ("SYNTH.NS", START, END) and timeout > 0     # a timeout on every request


@pytest.mark.parametrize("error, kind", [
    (_named_error("YFRateLimitError", "Too Many Requests. Rate limited."), "rate_limited"),
    (_named_error("YFPricesMissingError"), "not_found"),
    (_named_error("YFTzMissingError"), "not_found"),
    (_named_error("YFDataException"), "bad_response"),
    (ConnectionError("reset"), "unavailable"),
    (TimeoutError("timed out"), "unavailable"),
])
def test_yfinance_errors_map_to_provider_error_kinds(error, kind):
    assert classify_error(error) == kind


def test_yfinance_rate_limit_is_not_retried():
    fetch, sleeps = Recorder(_named_error("YFRateLimitError")), []
    provider = YFinanceProvider(history_fetch=fetch, sleep=sleeps.append)
    with pytest.raises(ProviderError) as info:
        provider.get_daily_prices("SYNTH.NS", START, END)
    assert info.value.kind == "rate_limited" and info.value.provider == "yfinance"
    assert len(fetch.calls) == 1 and sleeps == []


def test_yfinance_network_error_is_retried_then_succeeds():
    fetch, sleeps = Recorder(ConnectionError("reset"), history_frame()), []
    provider = YFinanceProvider(history_fetch=fetch, sleep=sleeps.append, clock=lambda: AFTER_CLOSE)
    assert len(provider.get_daily_prices("SYNTH.NS", START, END)) == 6
    assert len(fetch.calls) == 2 and sleeps == [2.0]


def test_yfinance_empty_table_is_not_found():
    provider = YFinanceProvider(history_fetch=Recorder(pd.DataFrame()))
    with pytest.raises(ProviderError) as info:
        provider.get_daily_prices("NOPE.NS", START, END)
    assert info.value.kind == "not_found"


def test_offline_retry_that_returns_an_empty_table_is_an_outage_not_a_missing_symbol():
    """Found by the offline smoke run: DNSError first, then yfinance quietly returns an empty table."""
    fetch = Recorder(ConnectionError("Could not resolve host"), pd.DataFrame())
    with pytest.raises(ProviderError) as info:
        YFinanceProvider(history_fetch=fetch, sleep=lambda s: None).get_daily_prices("SYNTH.NS", START, END)
    assert info.value.kind == "unavailable" and len(fetch.calls) == 2


def test_an_empty_table_with_a_logged_network_failure_is_an_outage():
    import logging

    def fetch(*args):
        logging.getLogger("yfinance").error("Failed to get ticker 'SYNTH.NS' reason: Failed to perform, curl: (6)")
        return pd.DataFrame()

    with pytest.raises(ProviderError) as info:
        YFinanceProvider(history_fetch=fetch).get_daily_prices("SYNTH.NS", START, END)
    assert info.value.kind == "unavailable"
    assert not any(type(h).__name__ == "_NetworkTroubleHandler" for h in logging.getLogger("yfinance").handlers)


def test_yfinance_non_table_reply_is_a_bad_response():
    provider = YFinanceProvider(history_fetch=Recorder({"not": "a frame"}))
    with pytest.raises(ProviderError) as info:
        provider.get_daily_prices("SYNTH.NS", START, END)
    assert info.value.kind == "bad_response"


def test_yfinance_rejects_a_bad_symbol_before_any_request():
    fetch = Recorder(history_frame())
    with pytest.raises(ValueError):
        YFinanceProvider(history_fetch=fetch).get_daily_prices("REL;DROP", START, END)
    assert fetch.calls == []


# --- yfinance news ---

def test_yfinance_news_handles_both_shapes_and_skips_bad_items():
    items = raw_news_to_items("SYNTH.NS", news_fixture())
    assert [i.url.rsplit("/", 1)[-1] for i in items] == ["plant", "board", "results"]   # newest first
    assert items[0].publisher == "Example Wire" and items[1].publisher == "Example Daily"
    assert items[2].headline == "Synthetic Corp quarterly results announced"          # spaces tidied
    assert all(i.published_at.tzinfo is not None for i in items)


def test_yfinance_news_limit():
    provider = YFinanceProvider(news_fetch=Recorder(news_fixture()))
    assert len(provider.get_news("SYNTH.NS", 2)) == 2


# --- Google News RSS ---

def test_rss_feed_parsing_strips_publisher_suffix_and_skips_bad_items():
    items = parse_feed("SYNTH.NS", rss_fixture())
    assert [i.url[-1] for i in items] == ["1", "3", "2"]                               # newest first
    assert items[0].headline == "Synthetic Corp shares rise after record profit"
    assert items[0].publisher == "Example Times"
    assert items[1].publisher is None                                                  # no <source>
    assert items[2].headline == "Synthetic Corp faces probe over delayed filings"
    assert items[1].published_at == datetime(2026, 11, 7, 7, 0, tzinfo=timezone.utc)   # +0530 -> UTC


def test_rss_provider_builds_an_escaped_url_and_applies_the_limit():
    fetch = Recorder(rss_fixture())
    items = GoogleNewsRssProvider(fetch=fetch).get_news("reliance.ns", 2)
    assert len(items) == 2 and items[0].symbol == "RELIANCE.NS"
    url, timeout = fetch.calls[0]
    assert url.startswith(rss.FEED_URL + "?q=RELIANCE+share&") and "ceid=IN%3Aen" in url and timeout > 0


def test_index_symbols_are_searched_by_name():
    assert news_query("^NSEI") == "Nifty 50" and news_query("TCS.NS") == "TCS share"


@pytest.mark.parametrize("body", [b"<html><body>not a feed</body></html>", b"<rss><channel>", b"\x00\x01"])
def test_rss_garbage_is_a_bad_response(body):
    with pytest.raises(ProviderError) as info:
        GoogleNewsRssProvider(fetch=Recorder(body)).get_news("TCS.NS", 5)
    assert info.value.kind == "bad_response"


def test_rss_rejects_a_bad_symbol_before_any_request():
    fetch = Recorder(rss_fixture())
    with pytest.raises(ValueError):
        GoogleNewsRssProvider(fetch=fetch).get_news("TCS.NS&q=x", 5)
    assert fetch.calls == []


@pytest.mark.parametrize("error, kind", [
    (urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None), "rate_limited"),
    (urllib.error.HTTPError("u", 404, "Not Found", {}, None), "not_found"),
    (urllib.error.HTTPError("u", 503, "Unavailable", {}, None), "unavailable"),
    (urllib.error.HTTPError("u", 400, "Bad Request", {}, None), "bad_response"),
    (urllib.error.URLError("no route"), "unavailable"),
    (TimeoutError("timed out"), "unavailable"),
])
def test_rss_http_errors_map_to_provider_error_kinds(monkeypatch, error, kind):
    def fake_urlopen(request, timeout):
        assert timeout > 0
        raise error
    monkeypatch.setattr(rss.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ProviderError) as info:
        rss._urllib_fetch("https://news.google.com/rss/search?q=x", 5)
    assert info.value.kind == kind


def test_rss_oversized_reply_is_refused(monkeypatch):
    class Big(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(rss.urllib.request, "urlopen",
                        lambda request, timeout: Big(b"x" * (rss.MAX_RESPONSE_BYTES + 10)))
    with pytest.raises(ProviderError) as info:
        rss._urllib_fetch("https://news.google.com/rss/search?q=x", 5)
    assert info.value.kind == "bad_response"


def test_rss_temporary_failure_is_retried():
    fetch, sleeps = Recorder(ProviderError("unavailable", "503"), rss_fixture()), []
    assert len(GoogleNewsRssProvider(fetch=fetch, sleep=sleeps.append).get_news("TCS.NS", 5)) == 3
    assert sleeps == [2.0]


# --- Factory and boundaries ---

def test_factory_builds_the_configured_adapters():
    assert isinstance(get_price_provider(), YFinanceProvider)          # MARKET_DATA_PROVIDER default
    assert isinstance(get_news_provider(), GoogleNewsRssProvider)      # NEWS_PROVIDER default
    assert isinstance(get_news_provider("yfinance"), YFinanceProvider)
    with pytest.raises(ValueError, match="MARKET_DATA_PROVIDER"):
        get_price_provider("alphavantage")                             # not built (spike decision)


def test_only_the_adapter_imports_yfinance():
    """Same rule as the LLM layer: no vendor import outside market_data/, and there only in the adapter."""
    importers = sorted(
        str(path.relative_to(APP_DIR)).replace("\\", "/") for path in APP_DIR.rglob("*.py")
        if "import yfinance" in path.read_text(encoding="utf-8")
    )
    assert importers == ["market_data/yfinance_provider.py"]
