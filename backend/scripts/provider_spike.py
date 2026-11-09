# backend/scripts/provider_spike.py
#
# Purpose: THROWAWAY provider spike for Phase 7 (Prompt 7A-0 in docs/phase7-spec.md).
# It is NOT app code and nothing in app/ imports it. It answers one question before
# we build anything: "from the developer's own machine, which free market-data and
# news sources actually return Indian (NSE/BSE) data, and how much of it?"
#
# Candidates (each needs no new dependency, or only the one listed):
#   1. Alpha Vantage over plain HTTPS (urllib). Key from ALPHAVANTAGE_API_KEY.
#   2. yfinance (unofficial Yahoo Finance route). Install it ONLY inside the running
#      container for this spike:  docker compose exec backend pip install yfinance
#      It is never added to requirements.txt by this script.
#   3. Google News RSS search, standard library only (urllib + xml.etree).
#
# What is recorded: did it return data, number of daily bars, years of history,
# first/latest date, number of headlines, error or rate-limit text, time taken.
# What is NOT recorded: prices, headlines, URLs, or the API key. The findings file
# contains counts and dates only (redistributing raw market data can break terms).
#
# Politeness rules: a timeout on every request, at most 2 retries (with backoff)
# for network errors only, never retry a rate-limit reply, a pause between calls,
# and at most 8 Alpha Vantage requests per run (free tier: 25/day, 5/minute).
#
# Run (from the repo root, with `docker compose up` running):
#   docker compose exec backend pip install yfinance          # spike only
#   docker compose exec backend python -m scripts.provider_spike
# Options: --skip-alphavantage  --skip-yfinance  --skip-rss  --out PATH
# The container only mounts backend/, so by default the file is written to
# backend/phase7-provider-spike.md; move it to docs/ afterwards (the script prints
# the path). On the host, where docs/ exists, it writes to docs/ directly.

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------- settings ---

TIMEOUT_SECONDS = 20          # every single HTTP request has this timeout
MAX_RETRIES = 2               # network errors only; rate-limit replies are never retried
BACKOFF_SECONDS = (5, 15)     # wait before retry 1 and retry 2
AV_PAUSE_SECONDS = 15         # Alpha Vantage free tier allows 5 calls/minute -> 12 s minimum
YF_PAUSE_SECONDS = 3
RSS_PAUSE_SECONDS = 3
AV_MAX_CALLS = 8              # hard cap per run, far under the 25/day free limit

# A generic client name (no personal contact details in headers).
USER_AGENT = "capstone-provider-spike/0.1 (educational research script)"

AV_URL = "https://www.alphavantage.co/query"

# Three large Indian stocks plus an index proxy. Base codes are exchange tickers.
STOCKS = ["RELIANCE", "TCS", "HDFCBANK"]
AV_INDEX_PROXY = "NIFTYBEES"   # Nippon India ETF Nifty BeES: tracks the Nifty 50 index
YF_INDEX = "^NSEI"             # Yahoo symbol for the Nifty 50 index itself

# Plain-English names give better news-search results than ticker codes.
NEWS_QUERIES = {
    "RELIANCE": "Reliance Industries share",
    "TCS": "Tata Consultancy Services share",
    "HDFCBANK": "HDFC Bank share",
}

logger = logging.getLogger("provider_spike")


# ------------------------------------------------------------ result rows ---

@dataclass
class PriceFinding:
    """One price request: counts and dates only, never the prices themselves."""
    provider: str
    symbol: str
    request: str
    ok: bool = False
    bars: int = 0
    first_date: date | None = None
    latest_date: date | None = None
    seconds: float = 0.0
    note: str = ""

    @property
    def years(self) -> float:
        if self.first_date and self.latest_date:
            return (self.latest_date - self.first_date).days / 365.25
        return 0.0

    @property
    def lag_days(self) -> int | None:
        """Calendar days between the latest bar and today (weekends count)."""
        return (date.today() - self.latest_date).days if self.latest_date else None


@dataclass
class NewsFinding:
    """One news request: counts and dates only, never the headlines themselves."""
    provider: str
    query: str
    ok: bool = False
    headlines: int = 0
    latest: datetime | None = None
    last_7_days: int = 0
    with_publisher: int = 0
    seconds: float = 0.0
    note: str = ""


@dataclass
class SpikeReport:
    prices: list[PriceFinding] = field(default_factory=list)
    news: list[NewsFinding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    av_calls: int = 0


# ---------------------------------------------------------------- helpers ---

def scrub(text: str, secret: str | None) -> str:
    """Remove the API key from any text before it is printed or saved, and shorten it."""
    if secret:
        text = text.replace(secret, "***")
    text = " ".join(text.split())          # one line, for the markdown table
    text = text.replace("|", "/")          # a pipe would break the table
    return text[:220] + ("..." if len(text) > 220 else "")


def http_get(url: str) -> bytes:
    """GET with a timeout and at most MAX_RETRIES retries on NETWORK errors.

    HTTP 429 is treated as a rate limit and is not retried (retrying a rate
    limit only burns quota and annoys the provider).
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code < 500:
                raise                      # rate limit or client error: do not retry
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
        if attempt < MAX_RETRIES:
            wait = BACKOFF_SECONDS[attempt]
            logger.info("  network error, retry %d/%d in %ds", attempt + 1, MAX_RETRIES, wait)
            time.sleep(wait)
    assert last_error is not None
    raise last_error


def parse_iso_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


# ----------------------------------------------------------- Alpha Vantage ---

class AlphaVantageSpike:
    """Probes Alpha Vantage with a strict per-run call budget."""

    def __init__(self, api_key: str, report: SpikeReport) -> None:
        self._key = api_key
        self._report = report
        self._stopped_reason: str | None = None   # set when the daily limit is hit

    def _call(self, params: dict[str, str]) -> tuple[dict[str, Any] | None, str, float]:
        """One request. Returns (json or None, problem text, seconds)."""
        if self._stopped_reason:
            return None, f"skipped: {self._stopped_reason}", 0.0
        if self._report.av_calls >= AV_MAX_CALLS:
            return None, "skipped: per-run call budget used", 0.0
        if self._report.av_calls > 0:
            time.sleep(AV_PAUSE_SECONDS)
        self._report.av_calls += 1
        url = AV_URL + "?" + urllib.parse.urlencode({**params, "apikey": self._key})
        start = time.perf_counter()
        try:
            payload = json.loads(http_get(url).decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return None, scrub(f"HTTP {exc.code} {exc.reason}", self._key), time.perf_counter() - start
        except Exception as exc:  # noqa: BLE001 - a spike records every failure kind
            return None, scrub(f"{type(exc).__name__}: {exc}", self._key), time.perf_counter() - start
        seconds = time.perf_counter() - start

        # Alpha Vantage answers HTTP 200 even for errors; the body says what happened.
        for key in ("Note", "Information", "Error Message"):
            if key in payload:
                text = scrub(f"{key}: {payload[key]}", self._key)
                lowered = text.lower()
                if "per day" in lowered or ("daily" in lowered and "limit" in lowered):
                    self._stopped_reason = "daily rate limit reached"
                return payload, text, seconds
        return payload, "", seconds

    def symbol_search(self, keywords: str) -> list[str]:
        """Returns the symbols the documented search endpoint offers (e.g. RELIANCE.BSE)."""
        payload, problem, seconds = self._call({"function": "SYMBOL_SEARCH", "keywords": keywords})
        matches = (payload or {}).get("bestMatches", []) if not problem else []
        symbols = [m.get("1. symbol", "") for m in matches]
        regions = sorted({m.get("4. region", "") for m in matches})
        indian = [s for s in symbols if s.endswith((".BSE", ".NSE", ".BOM", ".NS", ".BO"))]
        self._report.notes.append(
            f"Alpha Vantage SYMBOL_SEARCH '{keywords}' ({seconds:.1f}s): {len(symbols)} matches; "
            f"Indian-style symbols: {', '.join(indian) or 'none'}; regions: {', '.join(regions) or '-'}"
            + (f"; problem: {problem}" if problem else "")
        )
        return symbols

    def daily(self, symbol: str, outputsize: str = "compact") -> None:
        finding = PriceFinding("Alpha Vantage", symbol, f"TIME_SERIES_DAILY {outputsize}")
        payload, problem, finding.seconds = self._call(
            {"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": outputsize}
        )
        series = (payload or {}).get("Time Series (Daily)", {})
        dates = sorted(d for d in (parse_iso_date(k) for k in series) if d)
        if dates:
            finding.ok = True
            finding.bars = len(dates)
            finding.first_date, finding.latest_date = dates[0], dates[-1]
        finding.note = problem or ("" if dates else "no 'Time Series (Daily)' in response")
        self._report.prices.append(finding)

    def news(self, ticker: str) -> None:
        finding = NewsFinding("Alpha Vantage NEWS_SENTIMENT", f"tickers={ticker}")
        payload, problem, finding.seconds = self._call(
            {"function": "NEWS_SENTIMENT", "tickers": ticker, "limit": "50"}
        )
        feed = (payload or {}).get("feed", []) if not problem else []
        stamps = []
        for item in feed:
            try:   # format: 20261108T153000
                stamps.append(datetime.strptime(item.get("time_published", ""), "%Y%m%dT%H%M%S")
                              .replace(tzinfo=timezone.utc))
            except ValueError:
                pass
        finding.ok = bool(feed)
        finding.headlines = len(feed)
        finding.with_publisher = sum(1 for item in feed if item.get("source"))
        _fill_news_dates(finding, stamps)
        finding.note = problem or ("" if feed else "empty feed")
        self._report.news.append(finding)


def run_alphavantage(report: SpikeReport) -> None:
    api_key = os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()
    if not api_key:
        report.notes.append("Alpha Vantage skipped: ALPHAVANTAGE_API_KEY is not set.")
        logger.warning("Alpha Vantage skipped: ALPHAVANTAGE_API_KEY not set")
        return
    logger.info("Alpha Vantage: key found (not shown). Budget %d calls, %ds apart.",
                AV_MAX_CALLS, AV_PAUSE_SECONDS)
    av = AlphaVantageSpike(api_key, report)

    # 1. Ask the documented search endpoint which suffixes exist for an Indian stock.
    logger.info("Alpha Vantage: SYMBOL_SEARCH RELIANCE")
    found = av.symbol_search("RELIANCE")
    nse_style = [s for s in found if s.startswith("RELIANCE.") and s.endswith((".NSE", ".NS"))]

    # 2. BSE-style daily prices (the suffix shown in Alpha Vantage's own docs).
    for base in STOCKS + [AV_INDEX_PROXY]:
        logger.info("Alpha Vantage: %s.BSE compact", base)
        av.daily(f"{base}.BSE")

    # 3. NSE-style symbol only if the documented search returned one.
    if nse_style:
        logger.info("Alpha Vantage: %s compact (NSE style from search)", nse_style[0])
        av.daily(nse_style[0])
    else:
        report.notes.append("Alpha Vantage: SYMBOL_SEARCH returned no NSE-style symbol for "
                            "RELIANCE, so no NSE symbol was tried (none is documented).")

    # 4. Does the free key get the full history? (outputsize=full)
    logger.info("Alpha Vantage: RELIANCE.BSE full")
    av.daily("RELIANCE.BSE", outputsize="full")

    # 5. Alpha Vantage's own news endpoint, as a possible news source.
    logger.info("Alpha Vantage: NEWS_SENTIMENT RELIANCE.BSE")
    av.news("RELIANCE.BSE")


# ---------------------------------------------------------------- yfinance ---

class _ListHandler(logging.Handler):
    """Collects yfinance's own log messages (it logs errors instead of raising)."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _with_retries(fn: Callable[[], Any], label: str) -> Any:
    """Run fn with at most MAX_RETRIES retries; never retry a rate-limit error."""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if "ratelimit" in type(exc).__name__.lower() or "too many requests" in str(exc).lower():
                raise
            if attempt == MAX_RETRIES:
                raise
            wait = BACKOFF_SECONDS[attempt]
            logger.info("  %s failed (%s), retry %d/%d in %ds",
                        label, type(exc).__name__, attempt + 1, MAX_RETRIES, wait)
            time.sleep(wait)
    return None


def _yf_news_time(item: dict[str, Any]) -> datetime | None:
    """yfinance's news shape changed between versions; handle both."""
    if "providerPublishTime" in item:
        return datetime.fromtimestamp(int(item["providerPublishTime"]), tz=timezone.utc)
    content = item.get("content") or {}
    stamp = content.get("pubDate") or content.get("displayTime")
    if stamp:
        try:
            return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def run_yfinance(report: SpikeReport) -> None:
    try:
        import yfinance as yf  # imported here so the script runs without it
    except ImportError:
        report.notes.append("yfinance skipped: not installed in this environment "
                            "(docker compose exec backend pip install yfinance).")
        logger.warning("yfinance not installed; skipped")
        return
    report.notes.append(f"yfinance version {getattr(yf, '__version__', '?')} (installed in the "
                        "container for this spike only, not in requirements.txt).")

    handler = _ListHandler()
    logging.getLogger("yfinance").addHandler(handler)

    symbols = [f"{s}.NS" for s in STOCKS] + [YF_INDEX]
    for i, symbol in enumerate(symbols):
        if i:
            time.sleep(YF_PAUSE_SECONDS)
        logger.info("yfinance: %s history(period=max)", symbol)
        handler.messages.clear()
        finding = PriceFinding("yfinance", symbol, "history period=max 1d")
        ticker = yf.Ticker(symbol)
        start = time.perf_counter()
        try:
            frame = _with_retries(
                lambda: ticker.history(period="max", interval="1d", auto_adjust=False,
                                       timeout=TIMEOUT_SECONDS),
                symbol,
            )
            if frame is not None and not frame.empty:
                dates = sorted(ts.date() for ts in frame.index)
                finding.ok = True
                finding.bars = len(dates)
                finding.first_date, finding.latest_date = dates[0], dates[-1]
            else:
                finding.note = "empty result"
        except Exception as exc:  # noqa: BLE001
            finding.note = scrub(f"{type(exc).__name__}: {exc}", None)
        finding.seconds = time.perf_counter() - start
        if handler.messages:
            finding.note = scrub("; ".join([finding.note, *handler.messages]).strip("; "), None)
        report.prices.append(finding)

        # yfinance also exposes a small news list per ticker: count it as a candidate.
        time.sleep(YF_PAUSE_SECONDS)
        news = NewsFinding("yfinance .news", symbol)
        start = time.perf_counter()
        try:
            items = _with_retries(lambda: ticker.news, f"{symbol} news") or []
            stamps = [t for t in (_yf_news_time(item) for item in items) if t]
            news.ok = bool(items)
            news.headlines = len(items)
            news.with_publisher = sum(
                1 for item in items
                if item.get("publisher") or (item.get("content") or {}).get("provider")
            )
            _fill_news_dates(news, stamps)
            news.note = "" if items else "empty list"
        except Exception as exc:  # noqa: BLE001
            news.note = scrub(f"{type(exc).__name__}: {exc}", None)
        news.seconds = time.perf_counter() - start
        report.news.append(news)

    logging.getLogger("yfinance").removeHandler(handler)


# -------------------------------------------------------- Google News RSS ---

def _fill_news_dates(finding: NewsFinding, stamps: list[datetime]) -> None:
    if not stamps:
        return
    now = datetime.now(timezone.utc)
    finding.latest = max(stamps)
    finding.last_7_days = sum(1 for s in stamps if (now - s).days < 7)


def run_google_news_rss(report: SpikeReport) -> None:
    redirect_links = 0
    total_links = 0
    for i, (base, query) in enumerate(NEWS_QUERIES.items()):
        if i:
            time.sleep(RSS_PAUSE_SECONDS)
        logger.info("Google News RSS: %s", query)
        finding = NewsFinding("Google News RSS", query)
        url = ("https://news.google.com/rss/search?"
               + urllib.parse.urlencode({"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}))
        start = time.perf_counter()
        try:
            root = ET.fromstring(http_get(url))
            items = root.findall("./channel/item")
            stamps = []
            for item in items:
                pub = item.findtext("pubDate")
                if pub:
                    try:
                        stamps.append(parsedate_to_datetime(pub))
                    except (TypeError, ValueError):
                        pass
                link = item.findtext("link") or ""
                total_links += 1
                redirect_links += "news.google.com" in link
            finding.ok = bool(items)
            finding.headlines = len(items)
            finding.with_publisher = sum(1 for item in items if (item.findtext("source") or "").strip())
            _fill_news_dates(finding, stamps)
            finding.note = "" if items else "no <item> elements"
        except urllib.error.HTTPError as exc:
            finding.note = scrub(f"HTTP {exc.code} {exc.reason}", None)
        except Exception as exc:  # noqa: BLE001
            finding.note = scrub(f"{type(exc).__name__}: {exc}", None)
        finding.seconds = time.perf_counter() - start
        report.news.append(finding)
    if total_links:
        report.notes.append(f"Google News RSS: {redirect_links}/{total_links} item links are "
                            "news.google.com redirect links (not the publisher URL).")


# ------------------------------------------------------------------ output ---

def _d(value: date | datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M UTC") if isinstance(value, datetime) else value.isoformat()


def render_markdown(report: SpikeReport) -> str:
    now = datetime.now(timezone.utc)
    lines = [
        "# Phase 7 provider spike: findings",
        "",
        f"Generated by `backend/scripts/provider_spike.py` on {now:%Y-%m-%d %H:%M} UTC, run from the "
        f"developer's machine (Python {platform.python_version()}, {platform.system()}).",
        "Counts and dates only: no prices, headlines or URLs are stored (provider terms).",
        f"Alpha Vantage requests used in this run: {report.av_calls} "
        f"(free tier: 25/day, 5/minute; per-run cap {AV_MAX_CALLS}).",
        "",
        "## Daily prices",
        "",
        "| Provider | Symbol | Request | Data? | Daily bars | Years | First date | Latest date "
        "| Lag (days) | Time (s) | Error / note |",
        "|---|---|---|---|---:|---:|---|---|---:|---:|---|",
    ]
    for p in report.prices:
        lines.append(
            f"| {p.provider} | `{p.symbol}` | {p.request} | {'yes' if p.ok else 'no'} | {p.bars} "
            f"| {p.years:.1f} | {_d(p.first_date)} | {_d(p.latest_date)} "
            f"| {p.lag_days if p.lag_days is not None else '-'} | {p.seconds:.1f} | {p.note or '-'} |"
        )
    lines += [
        "",
        "## News headlines",
        "",
        "| Provider | Query / symbol | Data? | Headlines | With publisher | Last 7 days | Latest "
        "| Time (s) | Error / note |",
        "|---|---|---|---:|---:|---:|---|---:|---|",
    ]
    for n in report.news:
        lines.append(
            f"| {n.provider} | {n.query} | {'yes' if n.ok else 'no'} | {n.headlines} "
            f"| {n.with_publisher} | {n.last_7_days} | {_d(n.latest)} | {n.seconds:.1f} "
            f"| {n.note or '-'} |"
        )
    lines += ["", "## Run notes", ""] + [f"- {note}" for note in report.notes]
    lines += ["", "<!-- Recommendation section is written by hand after reviewing the run. -->", ""]
    return "\n".join(lines)


def default_out_path() -> Path:
    """docs/ when it exists (host run); else backend/ (container only mounts backend/)."""
    here = Path(__file__).resolve()
    docs = here.parents[2] / "docs" if len(here.parents) > 2 else None
    if docs and docs.is_dir():
        return docs / "phase7-provider-spike.md"
    return here.parents[1] / "phase7-provider-spike.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 7 market-data provider spike")
    parser.add_argument("--skip-alphavantage", action="store_true")
    parser.add_argument("--skip-yfinance", action="store_true")
    parser.add_argument("--skip-rss", action="store_true")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    report = SpikeReport()
    if not args.skip_alphavantage:
        run_alphavantage(report)
    if not args.skip_yfinance:
        run_yfinance(report)
    if not args.skip_rss:
        run_google_news_rss(report)

    out = args.out or default_out_path()
    out.write_text(render_markdown(report), encoding="utf-8")
    logger.info("Findings written to %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
