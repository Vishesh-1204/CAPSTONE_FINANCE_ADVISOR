# app/market_data/rss_news_provider.py
#
# Purpose: headlines for a stock from the Google News RSS search feed, using ONLY the
# standard library (urllib + xml.etree). Chosen by the 7A-0 spike: 30-46 headlines per
# stock with a publisher on every item, versus at most 10 from yfinance.
#
# What is kept: headline text, link, publisher and time. Never the article body.
# Limitations (also in docs): links are news.google.com redirect links, not publisher URLs;
# the feed is meant for personal, non-commercial use; a search by ticker code can return
# headlines that are not about the company (no relevance filter yet).
#
# Politeness: one request per call, a timeout, at most 2 retries for temporary failures
# (call_with_retries), and the cache service calls it at most once per symbol per TTL.

import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

from app.core.stock_symbols import normalize_symbol
from app.market_data.base import REQUEST_TIMEOUT_SECONDS, NewsItem, ProviderError, call_with_retries

logger = logging.getLogger(__name__)

FEED_URL = "https://news.google.com/rss/search"
# Indian English edition (spike settings)
FEED_PARAMS = {"hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
# A generic client name: no personal contact details in request headers
USER_AGENT = "capstone-finance-advisor/0.7 (educational project)"
# A feed is ~100 KB; refuse anything far bigger rather than parse it
MAX_RESPONSE_BYTES = 2_000_000
MAX_HEADLINE_LENGTH = 500      # news_items.headline
MAX_URL_LENGTH = 2048          # news_items.url

# Index symbols have no company name in them; search for the index by its common name
INDEX_QUERY_NAMES = {"^NSEI": "Nifty 50", "^BSESN": "Sensex", "^NSEBANK": "Nifty Bank"}

Fetch = Callable[[str, float], bytes]


def news_query(symbol: str) -> str:
    """The search text for a symbol: 'RELIANCE.NS' -> 'RELIANCE share', '^NSEI' -> 'Nifty 50'."""
    if symbol in INDEX_QUERY_NAMES:
        return INDEX_QUERY_NAMES[symbol]
    return f"{symbol.split('.')[0]} share"


def _urllib_fetch(url: str, timeout: float) -> bytes:
    """GET with a timeout. Turns every failure into a ProviderError with the right kind."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise ProviderError("rate_limited", "HTTP 429 Too Many Requests", "google_rss") from exc
        if exc.code == 404:
            raise ProviderError("not_found", "HTTP 404", "google_rss") from exc
        if exc.code >= 500:
            raise ProviderError("unavailable", f"HTTP {exc.code}", "google_rss") from exc
        raise ProviderError("bad_response", f"HTTP {exc.code}", "google_rss") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        raise ProviderError("unavailable", f"{type(exc).__name__}: {exc}", "google_rss") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ProviderError("bad_response", "response too large", "google_rss")
    return body


def parse_feed(symbol: str, body: bytes) -> list[NewsItem]:
    """
    RSS XML -> NewsItems, newest first, one per link. Pure function (tests feed it fixtures).

    Items without a link, an http(s) link, a title or a parseable date are skipped, not guessed.
    Google appends " - Publisher" to every title; that suffix is removed (publisher is kept separately).
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ProviderError("bad_response", f"not valid XML: {exc}", "google_rss") from exc
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ProviderError("bad_response", "not an RSS feed", "google_rss")

    items: dict[str, NewsItem] = {}
    for element in channel.findall("item"):
        title = " ".join((element.findtext("title") or "").split())
        url = (element.findtext("link") or "").strip()
        publisher = " ".join((element.findtext("source") or "").split()) or None
        published = _parse_date(element.findtext("pubDate"))
        if not title or not url.startswith(("https://", "http://")) or len(url) > MAX_URL_LENGTH or not published:
            continue
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(f" - {publisher}")].rstrip()
        items.setdefault(url, NewsItem(symbol, title[:MAX_HEADLINE_LENGTH], url, publisher, published))
    return sorted(items.values(), key=lambda item: item.published_at, reverse=True)


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text.strip())
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:              # RSS dates should carry a zone; assume UTC if not
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class GoogleNewsRssProvider:
    """NewsProvider for Google News RSS search. `fetch` and `sleep` are injectable for tests."""

    name = "google_rss"

    def __init__(self, fetch: Fetch | None = None, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = REQUEST_TIMEOUT_SECONDS) -> None:
        self._fetch = fetch or _urllib_fetch
        self._sleep = sleep
        self._timeout = timeout

    def get_news(self, symbol: str, limit: int) -> list[NewsItem]:
        symbol = normalize_symbol(symbol)      # never build a URL from unchecked text
        if limit < 1:
            raise ValueError("limit must be at least 1")
        # urlencode escapes the query, so the symbol can never change the URL's structure
        url = FEED_URL + "?" + urllib.parse.urlencode({"q": news_query(symbol), **FEED_PARAMS})
        body = call_with_retries(lambda: self._fetch(url, self._timeout), label=f"google_rss {symbol}",
                                 sleep=self._sleep)
        items = parse_feed(symbol, body)[:limit]
        logger.info("google_rss %s: %d headlines", symbol, len(items))
        return items
