# app/schemas/stock.py
#
# Purpose: request/response shapes for the Phase 7 endpoints (/api/stocks/..., spec section 4).
#
# * Every symbol in a request goes through the strict `Symbol` type (core/stock_symbols.py):
#   trimmed, upper-cased, letters/digits/dot/dash/leading caret only, at most 20 characters.
# * Fixed value sets are Literal types. They must match the model constants and the pure
#   modules (tests/test_stocks_data_model.py checks that they do).
# * Responses use float, like the other schemas, because JSON has no Decimal type; the
#   database keeps the exact Numeric(14,4) prices.
# * Every response carries `disclaimer`: this is an educational analysis tool, and the
#   disclaimer is added by code, never written by the LLM.
# * Indicator fields are plain descriptive facts (spec section 5.10-5.11). There is no
#   "signal", "rating" or "target" field on purpose: the API cannot express advice.

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, field_validator

from app.core.disclaimers import STOCKS_DISCLAIMER
from app.core.stock_symbols import normalize_symbol

DISCLAIMER = STOCKS_DISCLAIMER

# A validated, canonical symbol (see core/stock_symbols.py). Raises a 422 for anything else.
Symbol = Annotated[str, Field(examples=["RELIANCE.NS"]), AfterValidator(normalize_symbol)]

PriceSource = Literal["yfinance", "alphavantage", "csv_upload"]          # models.stock.PRICE_SOURCES
NewsSource = Literal["google_rss", "yfinance"]                           # models.stock.NEWS_SOURCES
ScorerName = Literal["lexicon", "finbert"]                               # models.stock.SCORER_NAMES
ProviderCallStatus = Literal["ok", "rate_limited", "unavailable", "not_found", "bad_response"]
MarketDataProviderName = Literal["yfinance", "alphavantage"]             # MARKET_DATA_PROVIDER values
SentimentLabel = Literal["positive", "neutral", "negative"]              # sentiment_aggregation labels
ExplanationSource = Literal["llm", "template"]
# Where the bars came from on this request (the fallback chain in spec section 5.3):
#   fresh = cache younger than the TTL, refreshed = provider called now, stale = provider failed
#   and older cached rows were used (with the "data_stale" warning)
CacheStatus = Literal["fresh", "refreshed", "stale"]
# Descriptive position of the last close against the 50 and 200 day averages
TrendLabel = Literal["above_both_averages", "below_both_averages", "between_averages", "not_enough_history"]
# RSI by the common convention (above 70 "high", below 30 "low"); a description, not a forecast
RsiLabel = Literal["high", "normal", "low"]

# Bounds (design choices for an educational tool on a free-tier data source)
MAX_PRICE_DAYS = 3650              # GET /{symbol}/prices?days= at most 10 years
MAX_HEADLINE_LENGTH = 500          # same as news_items.headline


class _WithDisclaimer(BaseModel):
    """Base for every response: the disclaimer is added by code, never by the LLM."""
    disclaimer: str = DISCLAIMER


# --- Watchlist ---

class WatchlistAddRequest(BaseModel):
    """POST /api/stocks/watchlist. The per-user limit is STOCK_WATCHLIST_LIMIT."""
    symbol: Symbol


class WatchlistItemOut(BaseModel):
    symbol: str
    added_at: datetime | None
    last_bar_date: date | None        # newest cached bar; None until the first refresh


class WatchlistResponse(_WithDisclaimer):
    items: list[WatchlistItemOut]
    limit: int


# --- Refresh and prices ---

class RefreshResponse(_WithDisclaimer):
    """POST /api/stocks/{symbol}/refresh: what the cache did and how fresh the data now is."""
    symbol: str
    cache_status: CacheStatus
    price_source: PriceSource | None
    news_source: NewsSource | None
    data_as_of: date | None           # newest bar available after the refresh
    bars_saved: int
    headlines_saved: int
    warnings: list[str]               # codes such as "data_stale", "news_unavailable"


class PriceBarOut(BaseModel):
    bar_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int | None
    sma_50: float | None              # None until there are 50 bars
    sma_200: float | None             # None until there are 200 bars


class PricesResponse(_WithDisclaimer):
    """GET /api/stocks/{symbol}/prices?days=: bars oldest first, with the chart averages."""
    symbol: str
    bars: list[PriceBarOut]
    sources: list[PriceSource]        # a series can mix provider bars and a CSV upload
    data_as_of: date | None
    warnings: list[str]


class PriceUploadResponse(_WithDisclaimer):
    """POST /api/stocks/{symbol}/prices/upload: the CSV fallback when no provider works."""
    symbol: str
    rows_read: int
    bars_saved: int
    rows_rejected: int
    data_as_of: date | None
    warnings: list[str]


# --- Sentiment ---

class HeadlineOut(BaseModel):
    headline: str
    url: str
    publisher: str | None
    published_at: datetime
    source: NewsSource
    sentiment_positive: float | None
    sentiment_negative: float | None
    sentiment_neutral: float | None
    scorer_name: ScorerName | None


class SentimentOut(BaseModel):
    """Mirrors services/stocks/sentiment_aggregation.AggregateSentiment, plus the headlines used."""
    score: float                      # -1 (all bad news) to +1 (all good news), recency-weighted
    label: SentimentLabel
    n_items: int
    reliable: bool                    # False: too few headlines, the UI shows "not enough headlines"
    effective_weight: float
    scorer: ScorerName
    headlines: list[HeadlineOut]


class SentimentScoreRequest(BaseModel):
    """
    POST /api/stocks/sentiment/score: score one headline (demos). `scorer` picks lexicon or FinBERT for this
    call (Phase 7C, so the page can show both side by side); omitted = the active SENTIMENT_SCORER. FinBERT
    asked for but not installed falls back to the lexicon with the warning "finbert_unavailable".
    """
    headline: str = Field(min_length=1, max_length=MAX_HEADLINE_LENGTH, examples=["Company reports record profit"])
    scorer: ScorerName | None = None

    @field_validator("headline")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("headline is blank")
        return value


class SentimentScoreResponse(_WithDisclaimer):
    positive: float
    negative: float
    neutral: float
    label: SentimentLabel
    scorer: ScorerName                # the scorer actually used (lexicon if FinBERT was unavailable)
    warnings: list[str]               # e.g. "finbert_unavailable"


# --- Analysis ---

class IndicatorsOut(BaseModel):
    """Descriptive facts about the PAST. None means "not enough history for this one" (with a warning)."""
    bars_used: int
    last_close: float
    sma_50: float | None
    sma_200: float | None
    trend: TrendLabel
    rsi_14: float | None
    rsi_label: RsiLabel | None
    annualized_volatility: float | None
    max_drawdown: float | None        # negative fraction: -0.25 = a 25% fall from the peak
    var_95_one_day: float | None      # positive loss fraction (historical, one day)
    change_1m: float | None           # price change over the last 21 trading days (fraction)
    change_1y: float | None           # price change over the last 252 trading days (fraction)
    benchmark: str | None             # e.g. "^NSEI"
    beta: float | None
    correlation: float | None


class StockAnalysisResponse(_WithDisclaimer):
    id: int
    symbol: str
    created_at: datetime | None
    data_as_of: date
    indicators: IndicatorsOut
    sentiment: SentimentOut | None    # None when no headlines could be loaded
    sources: dict[str, str]           # e.g. {"prices": "yfinance", "news": "google_rss", "cache": "fresh"}
    warnings: list[str]               # e.g. "data_stale", "insufficient_history", "finbert_unavailable"
    explanation: str | None
    explanation_source: ExplanationSource
    llm_used: bool
    explanation_faithful: bool | None
    claims_compliant: bool | None
    engine_version: str


class AnalysisHistoryItem(BaseModel):
    id: int
    symbol: str
    created_at: datetime | None
    data_as_of: date
    trend: TrendLabel
    explanation_source: ExplanationSource


class AnalysisHistoryResponse(_WithDisclaimer):
    analyses: list[AnalysisHistoryItem]


# --- Status ---

class ProviderStatusOut(BaseModel):
    provider: str
    calls_today: int
    daily_limit: int | None           # None when the provider publishes no fixed limit
    last_call_at: datetime | None
    last_status: ProviderCallStatus | None


class CacheAgeOut(BaseModel):
    symbol: str
    latest_bar_date: date | None
    fetched_at: datetime | None


class StockStatusResponse(_WithDisclaimer):
    """GET /api/stocks/status: which providers are active, quota used, cache ages, FinBERT yes/no."""
    market_data_provider: MarketDataProviderName
    news_provider: NewsSource
    sentiment_scorer: ScorerName
    finbert_available: bool
    providers: list[ProviderStatusOut]
    cache: list[CacheAgeOut]
