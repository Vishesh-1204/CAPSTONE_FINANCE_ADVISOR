# app/services/stock_service.py
#
# Purpose: business logic of the Stock Intelligence Agent (Phase 7, spec sections 4 and 8).
#   * watchlist   per-user symbols, with STOCK_WATCHLIST_LIMIT (protects the free data quota)
#   * refresh     prices + headlines through the cache chain (market_data/cache_service.py)
#   * prices      cache-only bars + 50/200-day averages for the chart (never calls a provider)
#   * upload      CSV fallback when no provider works (adds missing days only; data is shared)
#   * analyze     indicators + risk + benchmark beta + headline sentiment -> facts -> ONE
#                 explanation (agents/stock_explainer.py) -> stored in stock_analyses
#   * status      providers in use, calls today, cache ages, FinBERT installed yes/no
#
# Every number is computed HERE (with the pure modules in services/stocks/); the LLM only words it.
# Each indicator needs a minimum amount of history; with less it is None and the warning
# "insufficient_history" is added (fewer than 252 bars) - an analysis never crashes on short data.
# Security: user_id always comes from the JWT (routes); every user-data query filters by it.
# Price bars and headlines are SHARED public data (no user_id), but a user can only refresh,
# analyse or upload symbols on their own watchlist.

import csv
import io
import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.stock_explainer import StockExplainer
from app.core.config import settings
from app.core.stock_symbols import normalize_symbol
from app.market_data import cache_service
from app.market_data.base import IST, NewsProvider, PriceBar, PriceProvider
from app.market_data.factory import get_news_provider, get_price_provider
from app.models.stock import NewsItemRow, PriceBar as PriceBarRow, ProviderCall, StockAnalysis, WatchlistItem
from app.sentiment.factory import finbert_available, score_headlines
from app.services.investing.claims_guard import find_prohibited_stock_claims
from app.services.stocks.indicators import (
    TRADING_DAYS_PER_YEAR, annualized_volatility, max_drawdown, rsi, simple_returns, sma,
)
from app.services.stocks.risk_metrics import MIN_OBSERVATIONS_FOR_VAR, beta, correlation, historical_var
from app.services.stocks.sentiment_aggregation import HeadlineSentiment, aggregate_sentiment

logger = logging.getLogger(__name__)

ENGINE_VERSION = "7.1"                  # 7.1: claims guard v3.1 and provider backoff (Phase 7B fixes)

# Analysis windows (descriptive conventions, stored in inputs_json for reproducibility)
ANALYSIS_DAYS = 400                     # calendar days loaded: ~275 trading days, covers one year + averages
RISK_WINDOW = TRADING_DAYS_PER_YEAR     # 252 trading days = "the last year"
SMA_SHORT, SMA_LONG = 50, 200
RSI_PERIOD = 14
RSI_HIGH, RSI_LOW = 70.0, 30.0
VAR_CONFIDENCE = 0.95
MONTH_BARS = 21                         # trading days in about one month
MIN_RETURNS = MIN_OBSERVATIONS_FOR_VAR  # 20: fewer returns -> no volatility / VaR / beta
SENTIMENT_HALF_LIFE_DAYS = 3.0
SENTIMENT_MIN_HEADLINES = 3
NEWS_LIMIT = 30
CHART_WARMUP_DAYS = 300                 # extra calendar days read so the 200-day average starts on day one

# CSV upload limits
MAX_UPLOAD_BYTES = 1_000_000
MAX_UPLOAD_ROWS = 20_000
MAX_PRICE = Decimal("10000000000")      # Numeric(14,4) holds up to 10^10
PRICE_STEP = Decimal("0.0001")
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y")

INSUFFICIENT_HISTORY = "insufficient_history"
BENCHMARK_UNAVAILABLE = "benchmark_unavailable"
BENCHMARK_STALE = "benchmark_stale"
FEW_HEADLINES = "few_headlines"


# --- Seams the tests replace (monkeypatch), like investment_service._build_explainer ---

def _price_provider() -> PriceProvider:
    return get_price_provider()


def _news_provider() -> NewsProvider:
    return get_news_provider()


def _build_explainer() -> StockExplainer:
    return StockExplainer.from_settings(find_prohibited_stock_claims)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


# --- Watchlist ---

def _watchlist_rows(db: Session, user_id: int) -> list[WatchlistItem]:
    return list(db.scalars(select(WatchlistItem).where(WatchlistItem.user_id == user_id)
                           .order_by(WatchlistItem.added_at, WatchlistItem.id)))


def _latest_bar_dates(db: Session, symbols: list[str]) -> dict[str, date]:
    if not symbols:
        return {}
    rows = db.execute(select(PriceBarRow.symbol, func.max(PriceBarRow.bar_date))
                      .where(PriceBarRow.symbol.in_(symbols)).group_by(PriceBarRow.symbol)).all()
    return {symbol: newest for symbol, newest in rows}


def list_watchlist(db: Session, user_id: int) -> dict:
    rows = _watchlist_rows(db, user_id)
    latest = _latest_bar_dates(db, [row.symbol for row in rows])
    return {
        "items": [{"symbol": row.symbol, "added_at": row.added_at, "last_bar_date": latest.get(row.symbol)}
                  for row in rows],
        "limit": settings.STOCK_WATCHLIST_LIMIT,
    }


def add_to_watchlist(db: Session, user_id: int, symbol: str) -> dict:
    symbol = normalize_symbol(symbol)
    rows = _watchlist_rows(db, user_id)
    if any(row.symbol == symbol for row in rows):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{symbol} is already on your watchlist.")
    if len(rows) >= settings.STOCK_WATCHLIST_LIMIT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Your watchlist is full ({settings.STOCK_WATCHLIST_LIMIT} symbols). Remove one first.",
        )
    db.add(WatchlistItem(user_id=user_id, symbol=symbol, added_at=_now()))
    db.commit()
    return list_watchlist(db, user_id)


def remove_from_watchlist(db: Session, user_id: int, symbol: str) -> dict:
    row = db.scalars(select(WatchlistItem).where(WatchlistItem.user_id == user_id,
                                                 WatchlistItem.symbol == normalize_symbol(symbol))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{symbol} is not on your watchlist.")
    db.delete(row)
    db.commit()
    return list_watchlist(db, user_id)


def _require_watchlisted(db: Session, user_id: int, symbol: str) -> str:
    """Refresh/analysis/upload only for the user's own watchlist (that is what the limit protects)."""
    symbol = normalize_symbol(symbol)
    found = db.scalar(select(WatchlistItem.id).where(WatchlistItem.user_id == user_id, WatchlistItem.symbol == symbol))
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"{symbol} is not on your watchlist. Add it with POST /api/stocks/watchlist first.")
    return symbol


def _no_data(exc: cache_service.NoMarketDataError, code: int = status.HTTP_503_SERVICE_UNAVAILABLE) -> HTTPException:
    return HTTPException(code, str(exc))


# --- Refresh ---

def refresh(db: Session, user_id: int, symbol: str) -> dict:
    """Prices and headlines through the cache chain (the TTL keeps repeated clicks off the provider)."""
    symbol = _require_watchlisted(db, user_id, symbol)
    now = _now()
    try:
        prices = cache_service.get_prices(db, symbol, 1, _price_provider(), now=now)
    except cache_service.NoMarketDataError as exc:
        raise _no_data(exc) from exc
    news = cache_service.get_news(db, symbol, _news_provider(), limit=NEWS_LIMIT, now=now)
    return {
        "symbol": symbol, "cache_status": prices.cache_status, "price_source": prices.source,
        "news_source": news.source, "data_as_of": prices.data_as_of, "bars_saved": prices.saved,
        "headlines_saved": news.saved, "warnings": prices.warnings + news.warnings,
    }


# --- Prices for the chart (cache only) ---

def get_chart_prices(db: Session, user_id: int, symbol: str, days: int) -> dict:
    symbol = _require_watchlisted(db, user_id, symbol)
    now = _now()
    try:
        result = cache_service.read_prices(db, symbol, days + CHART_WARMUP_DAYS, now=now)
    except cache_service.NoMarketDataError as exc:
        raise _no_data(exc, status.HTTP_404_NOT_FOUND) from exc
    closes = [float(bar.close) for bar in result.bars]
    short, long = sma(closes, SMA_SHORT), sma(closes, SMA_LONG)
    since = now.astimezone(IST).date() - timedelta(days=days)
    bars = [
        {"bar_date": bar.bar_date, "open": float(bar.open), "high": float(bar.high), "low": float(bar.low),
         "close": float(bar.close), "volume": bar.volume, "sma_50": short[i], "sma_200": long[i]}
        for i, bar in enumerate(result.bars) if bar.bar_date >= since
    ]
    sources = sorted(set(db.scalars(select(PriceBarRow.source).where(
        PriceBarRow.symbol == symbol, PriceBarRow.bar_date >= since)).all()))
    return {"symbol": symbol, "bars": bars, "sources": sources, "data_as_of": result.data_as_of,
            "warnings": result.warnings}


# --- CSV upload (fallback) ---

def _parse_date(text: str) -> date | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_price(text: str | None) -> Decimal | None:
    try:
        value = Decimal((text or "").replace(",", "").strip())
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0 or value >= MAX_PRICE:
        return None
    return value.quantize(PRICE_STEP, rounding=ROUND_HALF_UP)


def parse_price_csv(symbol: str, content: bytes, today: date) -> tuple[list[PriceBar], int]:
    """CSV -> (valid bars, rejected row count). Columns: date, open, high, low, close[, volume]."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The file is not UTF-8 text.") from exc
    reader = csv.DictReader(io.StringIO(text))
    headers = {(name or "").strip().lower().replace(" ", "_"): name for name in reader.fieldnames or []}
    missing = [column for column in ("date", "open", "high", "low", "close") if column not in headers]
    if missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"The CSV needs the columns date, open, high, low, close (volume optional); "
                            f"missing: {', '.join(missing)}.")
    bars: dict[date, PriceBar] = {}
    rejected = 0
    for number, row in enumerate(reader, start=1):
        if number > MAX_UPLOAD_ROWS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"At most {MAX_UPLOAD_ROWS} rows per file.")
        get = lambda column: row.get(headers[column]) if column in headers else None
        bar_date = _parse_date(get("date") or "")
        o, h, l, c = (_parse_price(get(column)) for column in ("open", "high", "low", "close"))
        if bar_date is None or bar_date > today or None in (o, h, l, c) or l > h:
            rejected += 1
            continue
        volume_text = (get("volume") or "").replace(",", "").strip()
        volume = int(Decimal(volume_text)) if volume_text.replace(".", "", 1).isdigit() else None
        bars[bar_date] = PriceBar(symbol, bar_date, o, h, l, c, volume)     # a repeated date: the last row wins
    return [bars[d] for d in sorted(bars)], rejected


def upload_prices(db: Session, user_id: int, symbol: str, content: bytes) -> dict:
    symbol = _require_watchlisted(db, user_id, symbol)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The file is larger than 1 MB.")
    now = _now()
    bars, rejected = parse_price_csv(symbol, content, now.astimezone(IST).date())
    count = lambda: db.scalar(select(func.count()).select_from(PriceBarRow).where(PriceBarRow.symbol == symbol))
    before = count()
    if bars:
        cache_service.upsert_bars(db, bars, cache_service.CSV_UPLOAD, now, overwrite=False)
        db.commit()
    saved = count() - before
    warnings = []
    if rejected:
        warnings.append("rows_rejected")
    if bars and saved < len(bars):
        warnings.append("existing_days_kept")       # cached provider bars are never overwritten
    newest = db.scalar(select(func.max(PriceBarRow.bar_date)).where(PriceBarRow.symbol == symbol))
    return {"symbol": symbol, "rows_read": len(bars) + rejected, "bars_saved": saved, "rows_rejected": rejected,
            "data_as_of": newest, "warnings": warnings}


# --- Analysis: indicators (pure, on oldest-first bars) ---

def _change(closes: list[float], bars_back: int) -> float | None:
    return closes[-1] / closes[-1 - bars_back] - 1 if len(closes) > bars_back else None


def _aligned_returns(bars: list[PriceBar], benchmark: list[PriceBar]) -> tuple[list[float], list[float]]:
    """Daily returns of both series on the dates they share, over the risk window."""
    bench_close = {bar.bar_date: float(bar.close) for bar in benchmark}
    common = [(float(bar.close), bench_close[bar.bar_date]) for bar in bars if bar.bar_date in bench_close]
    common = common[-(RISK_WINDOW + 1):]
    if len(common) < 2:
        return [], []
    return simple_returns([a for a, _ in common]), simple_returns([b for _, b in common])


def compute_indicators(bars: list[PriceBar], benchmark: list[PriceBar] | None) -> dict:
    """Every indicator the history allows; the rest are None. Never raises for short history."""
    closes = [float(bar.close) for bar in bars]
    window = closes[-(RISK_WINDOW + 1):]
    returns = simple_returns(window) if len(window) >= 2 else []
    sma_50 = sma(closes, SMA_SHORT)[-1] if len(closes) >= SMA_SHORT else None
    sma_200 = sma(closes, SMA_LONG)[-1] if len(closes) >= SMA_LONG else None
    last = closes[-1]

    if sma_50 is None or sma_200 is None:
        trend = "not_enough_history"
    elif last > sma_50 and last > sma_200:
        trend = "above_both_averages"
    elif last < sma_50 and last < sma_200:
        trend = "below_both_averages"
    else:
        trend = "between_averages"

    rsi_value = rsi(closes, RSI_PERIOD)[-1] if len(closes) > RSI_PERIOD else None
    rsi_label = None
    if rsi_value is not None:
        rsi_label = "high" if rsi_value > RSI_HIGH else "low" if rsi_value < RSI_LOW else "normal"

    beta_value = correlation_value = None
    if benchmark:
        asset_returns, bench_returns = _aligned_returns(bars, benchmark)
        if len(asset_returns) >= MIN_RETURNS:
            try:
                beta_value = beta(asset_returns, bench_returns)
                correlation_value = correlation(asset_returns, bench_returns)
            except ValueError:            # a flat series (zero variance): no meaningful beta
                beta_value = correlation_value = None

    enough = len(returns) >= MIN_RETURNS
    return {
        "bars_used": len(closes),
        "last_close": last,
        "sma_50": sma_50,
        "sma_200": sma_200,
        "trend": trend,
        "rsi_14": rsi_value,
        "rsi_label": rsi_label,
        "annualized_volatility": annualized_volatility(returns) if enough else None,
        "max_drawdown": max_drawdown(window).depth if len(window) >= 2 else None,
        "var_95_one_day": historical_var(returns, VAR_CONFIDENCE) if enough else None,
        "change_1m": _change(closes, MONTH_BARS),
        "change_1y": _change(closes, RISK_WINDOW),
        "beta": beta_value,
        "correlation": correlation_value,
    }


# --- Analysis: sentiment ---

def _score_news(db: Session, symbol: str, items: list, now: datetime) -> tuple[dict | None, list[str]]:
    """Scores every headline (writing the scores to news_items) and aggregates them."""
    if not items:
        return None, []
    scored = score_headlines([item.headline for item in items])
    rows = {row.url: row for row in db.scalars(select(NewsItemRow).where(
        NewsItemRow.symbol == symbol, NewsItemRow.url.in_([item.url for item in items])))}
    headlines, weighted = [], []
    for item, probs in zip(items, scored.probabilities):
        row = rows.get(item.url)
        if row is not None:
            row.sentiment_positive, row.sentiment_negative, row.sentiment_neutral = probs.as_tuple()
            row.scorer_name = scored.scorer
        age = max(0.0, (now - _utc(item.published_at)).total_seconds() / 86400)
        weighted.append(HeadlineSentiment(probs.positive, probs.negative, probs.neutral, age))
        headlines.append({
            "headline": item.headline, "url": item.url, "publisher": item.publisher,
            "published_at": _utc(item.published_at).isoformat(), "source": row.source if row else "google_rss",
            "sentiment_positive": probs.positive, "sentiment_negative": probs.negative,
            "sentiment_neutral": probs.neutral, "scorer_name": scored.scorer,
        })
    db.commit()
    aggregate = aggregate_sentiment(weighted, SENTIMENT_HALF_LIFE_DAYS, SENTIMENT_MIN_HEADLINES)
    warnings = list(scored.warnings) + ([] if aggregate.reliable else [FEW_HEADLINES])
    return {
        "score": aggregate.score, "label": aggregate.label, "n_items": aggregate.n_items,
        "reliable": aggregate.reliable, "effective_weight": aggregate.effective_weight,
        "scorer": scored.scorer, "headlines": headlines,
    }, warnings


# --- Analysis: orchestration ---

def analyze(db: Session, user_id: int, symbol: str) -> dict:
    symbol = _require_watchlisted(db, user_id, symbol)
    now = _now()
    price_provider, news_provider = _price_provider(), _news_provider()
    warnings: list[str] = []

    try:
        prices = cache_service.get_prices(db, symbol, ANALYSIS_DAYS, price_provider, now=now)
    except cache_service.NoMarketDataError as exc:
        raise _no_data(exc) from exc
    if not prices.bars:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"No price bars for {symbol} in the last {ANALYSIS_DAYS} days. Refresh it, or upload "
                            f"a CSV with POST /api/stocks/{symbol}/prices/upload.")
    warnings += prices.warnings
    if len(prices.bars) < RISK_WINDOW:
        warnings.append(INSUFFICIENT_HISTORY)

    benchmark_symbol = normalize_symbol(settings.STOCK_BENCHMARK_SYMBOL)
    benchmark_bars, benchmark = None, None
    if symbol != benchmark_symbol:                 # an index against itself has no meaningful beta
        benchmark = benchmark_symbol
        try:
            bench = cache_service.get_prices(db, benchmark_symbol, ANALYSIS_DAYS, price_provider, now=now)
            benchmark_bars = bench.bars
            if not bench.bars:
                warnings.append(BENCHMARK_UNAVAILABLE)
            elif bench.warnings:
                warnings.append(BENCHMARK_STALE)
        except cache_service.NoMarketDataError:
            warnings.append(BENCHMARK_UNAVAILABLE)

    indicators = compute_indicators(prices.bars, benchmark_bars)
    news = cache_service.get_news(db, symbol, news_provider, limit=NEWS_LIMIT, now=now)
    warnings += news.warnings
    sentiment, sentiment_warnings = _score_news(db, symbol, news.items, now)
    warnings += sentiment_warnings

    result = {
        "symbol": symbol, "is_index": symbol.startswith("^"), "benchmark": benchmark,
        "data_as_of": prices.data_as_of.isoformat(), "window": RISK_WINDOW, "indicators": indicators,
        "sentiment": {k: v for k, v in sentiment.items() if k != "headlines"} if sentiment else None,
        "warnings": list(dict.fromkeys(warnings)),          # keep order, drop duplicates
    }
    explanation = _build_explainer().explain(result)

    sources = {
        "prices": prices.source or "none", "prices_cache": prices.cache_status,
        "benchmark": benchmark or "none",
        "news": news.source or "none", "news_cache": news.cache_status or "none",
        "sentiment_scorer": sentiment["scorer"] if sentiment else "none",
    }
    row = StockAnalysis(
        user_id=user_id, symbol=symbol, created_at=now,
        inputs_json={
            "symbol": symbol, "benchmark": benchmark, "analysis_days": ANALYSIS_DAYS, "risk_window": RISK_WINDOW,
            "sma_windows": [SMA_SHORT, SMA_LONG], "rsi_period": RSI_PERIOD, "rsi_thresholds": [RSI_LOW, RSI_HIGH],
            "var_confidence": VAR_CONFIDENCE, "sentiment_scorer_setting": settings.SENTIMENT_SCORER,
            "sentiment_half_life_days": SENTIMENT_HALF_LIFE_DAYS, "sentiment_min_headlines": SENTIMENT_MIN_HEADLINES,
            "news_limit": NEWS_LIMIT, "cache_ttl_hours": settings.STOCK_CACHE_TTL_HOURS, "now": now.isoformat(),
        },
        result_json={
            **result,
            "sentiment": sentiment,                          # with the scored headlines
            "explanation_provider": explanation.provider,
            "explanation_rejected_for": list(explanation.rejected_for),
        },
        data_as_of=prices.data_as_of, sources_json=sources, engine_version=ENGINE_VERSION,
        explanation=explanation.text, llm_used=explanation.llm_used,
        explanation_faithful=explanation.explanation_faithful, claims_compliant=explanation.claims_compliant,
        llm_calls=explanation.llm_calls, raw_prohibited_claims=explanation.raw_prohibited_claims,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _analysis_response(row)


def _analysis_response(row: StockAnalysis) -> dict:
    result = row.result_json
    indicators = {**result["indicators"], "benchmark": result.get("benchmark")}
    sentiment = result.get("sentiment")
    return {
        "id": row.id, "symbol": row.symbol, "created_at": row.created_at, "data_as_of": row.data_as_of,
        "indicators": indicators, "sentiment": sentiment, "sources": row.sources_json,
        "warnings": result.get("warnings", []), "explanation": row.explanation,
        "explanation_source": row.explanation_source, "llm_used": row.llm_used,
        "explanation_faithful": row.explanation_faithful, "claims_compliant": row.claims_compliant,
        "engine_version": row.engine_version,
    }


def _analyses_query(user_id: int, symbol: str):
    return (select(StockAnalysis).where(StockAnalysis.user_id == user_id, StockAnalysis.symbol == symbol)
            .order_by(StockAnalysis.created_at.desc(), StockAnalysis.id.desc()))


def latest_analysis(db: Session, user_id: int, symbol: str) -> dict:
    symbol = normalize_symbol(symbol)
    row = db.scalars(_analyses_query(user_id, symbol).limit(1)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"No analysis of {symbol} yet. Generate one with POST /api/stocks/{symbol}/analysis.")
    return _analysis_response(row)


def list_analyses(db: Session, user_id: int, symbol: str, limit: int) -> dict:
    rows = db.scalars(_analyses_query(user_id, normalize_symbol(symbol)).limit(limit)).all()
    return {"analyses": [
        {"id": row.id, "symbol": row.symbol, "created_at": row.created_at, "data_as_of": row.data_as_of,
         "trend": row.result_json["indicators"]["trend"], "explanation_source": row.explanation_source}
        for row in rows
    ]}


def get_analysis(db: Session, user_id: int, symbol: str, analysis_id: int) -> dict:
    row = db.get(StockAnalysis, analysis_id)
    if row is None or row.user_id != user_id or row.symbol != normalize_symbol(symbol):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis not found.")
    return _analysis_response(row)


# --- Headline scorer (demo) ---

def score_one_headline(headline: str, scorer: str | None = None) -> dict:
    """One headline with `scorer` (None = the active SENTIMENT_SCORER); FinBERT falls back to the lexicon."""
    result = score_headlines([headline], scorer)
    probs = result.probabilities[0]
    return {"positive": probs.positive, "negative": probs.negative, "neutral": probs.neutral,
            "label": probs.label, "scorer": result.scorer, "warnings": result.warnings}


# --- Status ---

def get_status(db: Session, user_id: int) -> dict:
    now = _now()
    midnight = datetime.combine(now.astimezone(IST).date(), time(0, 0), tzinfo=IST).astimezone(timezone.utc)
    providers = []
    for name in dict.fromkeys([settings.MARKET_DATA_PROVIDER, settings.NEWS_PROVIDER]):
        calls_today = db.scalar(select(func.count()).select_from(ProviderCall).where(
            ProviderCall.provider == name, ProviderCall.at >= midnight))
        last = db.scalars(select(ProviderCall).where(ProviderCall.provider == name)
                          .order_by(ProviderCall.at.desc(), ProviderCall.id.desc()).limit(1)).first()
        providers.append({"provider": name, "calls_today": calls_today, "daily_limit": None,
                          "last_call_at": last.at if last else None, "last_status": last.status if last else None})

    symbols = list(dict.fromkeys([row.symbol for row in _watchlist_rows(db, user_id)]
                                 + [normalize_symbol(settings.STOCK_BENCHMARK_SYMBOL)]))
    latest = _latest_bar_dates(db, symbols)
    fetched = dict(db.execute(select(PriceBarRow.symbol, func.max(PriceBarRow.fetched_at))
                              .where(PriceBarRow.symbol.in_(symbols)).group_by(PriceBarRow.symbol)).all())
    return {
        "market_data_provider": settings.MARKET_DATA_PROVIDER, "news_provider": settings.NEWS_PROVIDER,
        "sentiment_scorer": settings.SENTIMENT_SCORER, "finbert_available": finbert_available(),
        "providers": providers,
        "cache": [{"symbol": s, "latest_bar_date": latest.get(s), "fetched_at": fetched.get(s)} for s in symbols],
    }
