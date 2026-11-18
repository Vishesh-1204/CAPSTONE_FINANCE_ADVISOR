# backend/scripts/seed_market_cache.py
#
# Purpose: fill the market-data cache ONCE while you are online, so demos (and the Phase 7
# smoke-test step) work later without internet (spec section 5.4). It goes through the normal
# cache_service chain, so it obeys the TTL, upserts without duplicates and logs provider_calls.
#
# Default list: the three large NSE stocks from the 7A-0 spike plus the benchmark
# (STOCK_BENCHMARK_SYMBOL, ^NSEI). It prints counts and dates only, never prices or headlines.
#
# Run (with `docker compose up` running and internet available):
#   docker compose exec backend python -m scripts.seed_market_cache
#   docker compose exec backend python -m scripts.seed_market_cache --symbols INFY.NS ITC.NS --force
# Options: --symbols ...   --force (ignore a fresh cache)   --no-news   --pause SECONDS
#
# The cached rows stay in YOUR local database only. They are downloaded market data: never
# commit or share a database dump that contains them (provider terms).

import argparse
import logging
import sys
import time

from app.core.config import settings
from app.core.stock_symbols import normalize_symbol
from app.db.session import SessionLocal
from app.market_data import cache_service
from app.market_data.factory import get_news_provider, get_price_provider

DEFAULT_SYMBOLS = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS"]

logger = logging.getLogger("seed_market_cache")


def seed(symbols: list[str], *, force: bool, with_news: bool, pause: float) -> int:
    """Fill the cache for each symbol. Returns the number of symbols that ended with NO data."""
    price_provider = get_price_provider()
    news_provider = get_news_provider() if with_news else None
    failures = 0
    with SessionLocal() as db:
        for i, symbol in enumerate(symbols):
            if i:
                time.sleep(pause)            # be polite: one symbol at a time, with a pause
            try:
                result = cache_service.get_prices(db, symbol, settings.STOCK_HISTORY_DAYS, price_provider,
                                                  force_refresh=force)
            except cache_service.NoMarketDataError as exc:
                failures += 1
                logger.error("%-12s prices: NONE (%s)", symbol, exc.kind)
                continue
            first = result.bars[0].bar_date if result.bars else None
            logger.info("%-12s prices: %-9s %5d bars  %s .. %s  source=%s%s", symbol, result.cache_status,
                        len(result.bars), first, result.data_as_of, result.source,
                        f"  warnings={result.warnings}" if result.warnings else "")
            if news_provider is not None:
                headlines = cache_service.get_news(db, symbol, news_provider, force_refresh=force)
                logger.info("%-12s news:   %-9s %5d headlines%s", symbol, headlines.cache_status or "none",
                            len(headlines.items), f"  warnings={headlines.warnings}" if headlines.warnings else "")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill the Phase 7 market-data cache while online")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS,
                        help="symbols to cache (the benchmark is always added)")
    parser.add_argument("--force", action="store_true", help="fetch even if the cache is fresh")
    parser.add_argument("--no-news", action="store_true", help="prices only")
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between symbols")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        symbols = [normalize_symbol(s) for s in args.symbols]
        benchmark = normalize_symbol(settings.STOCK_BENCHMARK_SYMBOL)
    except ValueError as exc:
        parser.error(str(exc))
    if benchmark not in symbols:
        symbols.append(benchmark)

    logger.info("Seeding %d symbols with %s prices / %s news, %d days of history",
                len(symbols), settings.MARKET_DATA_PROVIDER, "no" if args.no_news else settings.NEWS_PROVIDER,
                settings.STOCK_HISTORY_DAYS)
    failures = seed(symbols, force=args.force, with_news=not args.no_news, pause=args.pause)
    logger.info("Done: %d of %d symbols cached%s", len(symbols) - failures, len(symbols),
                "" if not failures else " (see errors above)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
