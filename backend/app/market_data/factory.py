# app/market_data/factory.py
#
# Purpose: builds market-data adapters from configuration (same pattern as llm/factory.py).
# The rest of the app asks this factory for "the price provider" and "the news provider" and
# never imports an adapter or a vendor library itself. Switching source = one .env variable:
#   MARKET_DATA_PROVIDER=yfinance            (the only price adapter built; see the spike)
#   NEWS_PROVIDER=google_rss | yfinance
# Adding a provider = write the adapter, add one line to a registry, add the name to the
# Literal in core/config.py.

from typing import Callable

from app.core.config import settings
from app.market_data.base import NewsProvider, PriceProvider
from app.market_data.rss_news_provider import GoogleNewsRssProvider
from app.market_data.yfinance_provider import YFinanceProvider

PRICE_PROVIDERS: dict[str, Callable[[], PriceProvider]] = {
    "yfinance": YFinanceProvider,
}
NEWS_PROVIDERS: dict[str, Callable[[], NewsProvider]] = {
    "google_rss": GoogleNewsRssProvider,
    "yfinance": YFinanceProvider,
}


def _build(registry: dict, name: str, setting: str):
    try:
        return registry[name]()
    except KeyError:
        raise ValueError(f"Unknown {setting} '{name}'. Choose one of: {', '.join(sorted(registry))}") from None


def get_price_provider(name: str | None = None) -> PriceProvider:
    """The adapter named by MARKET_DATA_PROVIDER (or `name`)."""
    return _build(PRICE_PROVIDERS, name or settings.MARKET_DATA_PROVIDER, "MARKET_DATA_PROVIDER")


def get_news_provider(name: str | None = None) -> NewsProvider:
    """The adapter named by NEWS_PROVIDER (or `name`)."""
    return _build(NEWS_PROVIDERS, name or settings.NEWS_PROVIDER, "NEWS_PROVIDER")
