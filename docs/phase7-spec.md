# Phase 7 Spec: Stock Intelligence Agent

Status: design + VERIFIED reference code (51 unit tests pass). Build on a branch cut from the updated `DEV`: `feature/vishesh-stocks`.
Everything here is an **educational analysis tool, not financial advice**. It describes the past and the news; it never says "buy", "sell" or what a price will do.

---

## 0. Decision record (needs to be in the repo)

- **2026-11-09, approved by Vishesh:** Phase 7 uses a **live market-data API** instead of user-uploaded files.
- **Why this is outside the approved stack:** the approved stack has no source of market prices or news. A data provider is unavoidable for this phase. Any new Python package it needs (for example `yfinance`) is approved ONLY for the data-provider adapter, behind an interface, and only after the provider spike (Prompt 7A-0) shows it works.
- **What we could and could not verify while writing this spec** (searched 2026-11-09):
  - Alpha Vantage free key: 25 requests per day and 5 per minute; end-of-day data only on the free plan.
  - Twelve Data free plan: 800 requests per day, but its pricing page lists only "US" markets for the free plan.
  - **Not verified for any provider: whether NSE/BSE Indian stocks and news are available on a free tier.** Also not verified: how many years of daily history the free tiers return, and the exact terms of use of the unofficial Yahoo Finance route (yfinance), which is not an official API and is widely reported to rate-limit.
  - Therefore **Prompt 7A-0 is a provider spike run on YOUR laptop**, and its result decides the provider before anything is built on top of it.

## 1. Overview

For each stock on a user's watchlist, the agent produces a plain-language **analysis snapshot** built from facts:

1. **Trend and momentum:** price against its 50 and 200 day averages, RSI.
2. **Risk:** volatility, worst fall (max drawdown), one-day Value at Risk, and beta against a benchmark index.
3. **News sentiment:** headlines scored by FinBERT (or a small word-list fallback), combined with newer news counting more, and flagged "not reliable" if there are too few headlines.
4. **Data freshness:** where the data came from and how old it is, always shown.
5. **An explanation** written by the LLM from those numbers only, checked by guards, with a template fallback.

It also adds a research-grade **direction-model evaluation** (walk-forward, baselines, significance test), and connects back to Phase 6 with a **historical bootstrap** version of the SIP simulation.

### Design rules inherited from Phases 4 to 6
Python does the maths, the LLM only explains. One LLM call per analysis. Aggregates only go to the LLM. Numeric guard plus a claims guard. Template fallback. Every query filters by `user_id` from the JWT. No test ever touches the network.

### What Phase 7 deliberately does NOT do
- No buy/sell/hold advice, no price targets, no "will rise" language.
- No intraday or real-time data, no trading, no brokerage connection.
- PyTorch (LSTM) is an optional stretch in a separate image profile. The default model is scikit-learn / LightGBM, which are in the approved stack and light to install.

## 2. Why It Is Needed

- Completes Module 5 of the project PDF (stock intelligence) and feeds Module 4 with real historical returns.
- Shows the system can handle **unreliable external data**: provider adapters, caching, fallback to stale data, CSV upload as a last resort. That is real engineering, and it makes the live demo safe.
- Gives the paper honest, testable questions: does FinBERT beat a lexicon on financial headlines, does aggregated sentiment lead returns at all, does any model beat simple baselines out of sample, and does a historical bootstrap change a SIP projection compared with the normal assumption?

## 3. Folder Location

```text
backend/app/
  services/stocks/
    __init__.py
    indicators.py              (reference code, pure)
    risk_metrics.py            (reference code, pure)
    sentiment_aggregation.py   (reference code, pure)
    lexicon_sentiment.py       (reference code, pure)
    walk_forward.py            (reference code, pure)
    bootstrap_returns.py       (reference code, pure; imports Phase 6 SimulationResult)
  market_data/
    base.py                    (MarketDataProvider protocol, PriceBar, NewsItem dataclasses)
    alphavantage_provider.py   (only if the spike approves it)
    yfinance_provider.py       (only if the spike approves it)
    rss_news_provider.py       (stdlib only, optional news source)
    factory.py                 (provider chosen by MARKET_DATA_PROVIDER in .env)
    cache_service.py           (database cache, freshness, fallback chain)
  sentiment/
    base.py                    (SentimentScorer protocol)
    lexicon_scorer.py          (wraps lexicon_sentiment, default)
    finbert_scorer.py          (HuggingFace, lazy import, optional)
  services/stock_service.py    (orchestration: watchlist, refresh, analysis)
  agents/stock_explainer.py    (ONE LLM call, guards, template fallback)
  models/stock.py, schemas/stock.py, api/routes/stocks.py
backend/migrations/versions/0006_stocks.py
backend/scripts/provider_spike.py     (7A-0, no app code)
backend/scripts/seed_market_cache.py  (fills the cache once while online, for safe demos)
backend/evaluation/evaluate_stocks.py (+ stocks_* helper files, same flat style as before)
backend/tests/test_stocks_*.py
frontend/app/(console)/stocks/page.tsx
docs/phase7-spec.md, docs/modules/stocks.md, docs/phase7-provider-spike.md
```

## 4. Module Documentation

### Purpose
Turn public market data and news into an explainable, honest description of a stock, and keep working when the data source does not.

### Architecture and flow

```text
 watchlist ──► refresh ──► cache_service ──► provider adapter ──► (Alpha Vantage / yfinance / RSS)
                               │  fresh in DB?  yes ► use it
                               │  no, provider ok ► save to price_bars / news_items
                               │  provider fails ► stale DB rows + warning "data_stale"
                               │  nothing at all ► clear error, CSV upload endpoint
                               ▼
        price_bars ──► indicators, risk_metrics ─┐
        news_items ──► SentimentScorer ──► sentiment_aggregation ─┤
        benchmark bars ──► beta                  ─┘
                               ▼
                  facts (aggregates, pre-formatted numbers)
                               ▼
              stock_explainer (1 LLM call) ► numeric guard + claims guard v3 ► fallback template
                               ▼
        stock_analyses row (stored with data_as_of, sources, warnings) ──► API ──► UI
```

### Input
A watchlist symbol (validated), cached or refreshed price bars (daily, oldest first), headlines, a benchmark symbol, and settings (TTL, windows, thresholds).

### Output (one stored analysis)
Trend label (above / below / between the 50 and 200 day averages), RSI value and convention label, annualised volatility, max drawdown, 95% one-day VaR, beta and correlation versus the benchmark, aggregate sentiment (score, label, number of headlines, reliable yes/no), data source, `data_as_of`, warnings, explanation, `explanation_source`, disclaimer.

### Database tables (migration 0006)
- `watchlist_items`: id, user_id (FK), symbol, added_at. Unique (user_id, symbol). Per-user limit (setting, default 10) to protect free API quotas.
- `price_bars` (shared public market data, not user data): id, symbol, bar_date, open, high, low, close, volume, source, fetched_at. Numeric(14,4). Unique (symbol, bar_date).
- `news_items` (shared): id, symbol, headline, url, publisher, published_at, source, sentiment_positive, sentiment_negative, sentiment_neutral, scorer_name, fetched_at. Unique (symbol, url). Store headline text and link only, not article bodies.
- `stock_analyses`: id, user_id (FK), symbol, created_at, inputs_json, result_json, explanation, llm_used, explanation_faithful, claims_compliant, llm_calls, raw_prohibited_claims, data_as_of, sources_json, engine_version. Index (user_id, created_at DESC).
- Also a small `provider_calls` log (provider, endpoint, status, at) so the status endpoint can show remaining quota and the paper can report availability.

### API design (all under `/api/stocks`, JWT required)

| Method | Path | Purpose |
|---|---|---|
| POST | `/watchlist` | Add a symbol (validated, per-user limit) |
| GET | `/watchlist` | List with last data date |
| DELETE | `/watchlist/{symbol}` | Remove |
| POST | `/{symbol}/refresh` | Pull prices and news through the cache (rate-limited), returns freshness |
| GET | `/{symbol}/prices?days=` | Daily bars with SMA 50 / 200 for the chart |
| POST | `/{symbol}/prices/upload` | CSV fallback when no provider works |
| POST | `/{symbol}/analysis` | Generate and store an analysis |
| GET | `/{symbol}/analysis/latest`, `/{symbol}/analyses` | Latest and history |
| GET | `/{symbol}/analyses/{id}` | One full stored analysis, scoped to the user |
| POST | `/sentiment/score` | Score one headline with the active scorer (great for demos) |
| GET | `/status` | Provider in use, calls made and left today, cache ages, FinBERT available yes/no |

### Testing strategy
1. Unit tests on the six pure modules (51 written and passing).
2. Provider adapters tested with recorded fake responses and a fake provider. **No test calls the network.**
3. Cache tests: fresh hit, miss then fetch, provider failure returns stale rows with a warning, nothing cached gives a clear error, duplicate bars do not duplicate rows.
4. Sentiment tests with a stub scorer; FinBERT test is skipped automatically when the model is not installed.
5. Explainer tests with a stub LLM: valid text accepted, invented number rejected, advice or prediction wording rejected, provider failure gives the template, exactly one call with a single stub.
6. Route tests: auth, user isolation, symbol validation, watchlist limit, disclaimer present.
7. Smoke test extended with one step that runs from the seeded cache, so it passes with no internet.

## 5. Key design decisions

1. **Provider behind an interface.** Same pattern as the LLM layer: no vendor import outside `market_data/`. Switching provider is one environment variable.
2. **Cache first, always.** Public market data is cached in the database and refreshed at most once per symbol per day (TTL setting). This protects a 25-requests-a-day quota, makes the app fast, and means the demo works if the internet does not.
3. **Fallback chain:** fresh cache, then provider, then stale cache with a `data_stale` warning, then CSV upload. The UI always shows source and `data_as_of`.
4. **Seed script for demos.** `seed_market_cache.py` fills the cache once while you are online, so the demo does not depend on a live call.
5. **No real market data in git.** Redistributing downloaded price data can break provider terms. Tests use small synthetic random-walk fixtures generated by code.
6. **Symbols are validated** with a strict pattern (letters, digits, dot, dash, caret, length limit) before they go into any URL or query. Never build URLs from raw user text.
7. **Secrets in the environment only:** `ALPHAVANTAGE_API_KEY`, `MARKET_DATA_PROVIDER`, `NEWS_PROVIDER`, `STOCK_CACHE_TTL_HOURS`, `STOCK_WATCHLIST_LIMIT`, `SENTIMENT_SCORER=lexicon|finbert`.
8. **FinBERT is optional and lazy.** It needs PyTorch and a downloaded model, which is heavy and slows Docker builds (you had slow pip downloads before). The default scorer is the lexicon. FinBERT runs from an optional Docker Compose profile (`docker compose --profile ml up`) with its own requirements file. The backend works fully without it, and the paper compares both.
9. **Claims guard v3 for stock text.** Add to the Phase 6 guard: advice wording ("you should buy / sell / hold / invest", "we recommend buying") and predictions ("will rise / fall / double / crash", "price target", "target price", "guaranteed"). It is deliberately strict. It must not flag neutral descriptions such as "the price fell 4% last month" or the phrase "buy-and-hold" used in a comparison. Test both directions.
10. **Descriptive labels, not signals.** "Trading above its 200-day average" is a fact. "Strong buy" is advice. Only facts are allowed in the output.
11. **Descriptive, not predictive.** RSI above 70 is labelled "high by the common convention", not "about to fall".

## 6. Reference code (VERIFIED: 51 tests pass)

Copy these files exactly. They import `SimulationResult` from the Phase 6 `monte_carlo.py`, which already exists in the repo.

**`app/services/stocks/indicators.py`**

```python
# app/services/stocks/indicators.py
#
# Purpose: classic, explainable price indicators computed in plain Python: returns, moving
# averages, RSI, volatility, drawdown and growth rate. Every number the Stock Intelligence
# Agent shows comes from here, so it can be tested and explained line by line.
# Pure functions: no database, no network, no LLM. Prices are oldest-first lists of closing prices.

import math
from dataclasses import dataclass

TRADING_DAYS_PER_YEAR = 252


def _check_prices(prices: list[float], minimum: int = 2) -> None:
    if len(prices) < minimum:
        raise ValueError(f"need at least {minimum} prices")
    if any(price <= 0 for price in prices):
        raise ValueError("prices must be positive")


def simple_returns(prices: list[float]) -> list[float]:
    """Day-to-day percentage change as a fraction (0.01 = +1%). One element shorter than prices."""
    _check_prices(prices)
    return [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))]


def log_returns(prices: list[float]) -> list[float]:
    """Natural-log returns; they add up over time, which is handy for statistics."""
    _check_prices(prices)
    return [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]


def sma(values: list[float], window: int) -> list[float | None]:
    """Simple moving average. The first window-1 entries are None (not enough history yet)."""
    if window <= 0:
        raise ValueError("window must be positive")
    result: list[float | None] = [None] * len(values)
    running = 0.0
    for i, value in enumerate(values):
        running += value
        if i >= window:
            running -= values[i - window]
        if i >= window - 1:
            result[i] = running / window
    return result


def ema(values: list[float], window: int) -> list[float | None]:
    """Exponential moving average, seeded with the SMA of the first window values."""
    if window <= 0:
        raise ValueError("window must be positive")
    result: list[float | None] = [None] * len(values)
    if len(values) < window:
        return result
    alpha = 2 / (window + 1)
    current = sum(values[:window]) / window
    result[window - 1] = current
    for i in range(window, len(values)):
        current = alpha * values[i] + (1 - alpha) * current
        result[i] = current
    return result


def rsi(prices: list[float], period: int = 14) -> list[float | None]:
    """
    Relative Strength Index (Wilder). 0-100: above ~70 is often called overbought, below ~30
    oversold. These are conventions, not predictions. None until `period` changes exist.
    """
    _check_prices(prices)
    if period <= 0:
        raise ValueError("period must be positive")
    result: list[float | None] = [None] * len(prices)
    if len(prices) <= period:
        return result
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    avg_gain = sum(max(c, 0.0) for c in changes[:period]) / period
    avg_loss = sum(max(-c, 0.0) for c in changes[:period]) / period

    def to_rsi(gain: float, loss: float) -> float:
        if gain == 0 and loss == 0:
            return 50.0
        if loss == 0:
            return 100.0
        return 100 - 100 / (1 + gain / loss)

    result[period] = to_rsi(avg_gain, avg_loss)
    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + max(changes[i], 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-changes[i], 0.0)) / period
        result[i + 1] = to_rsi(avg_gain, avg_loss)
    return result


def _sample_std(values: list[float]) -> float:
    # math.fsum adds floats exactly. Plain sum() on Python 3.11 leaves rounding residue
    # (30 x 0.001 averages to 0.0010000000000000007), so a constant series got a tiny
    # non-zero std instead of exactly 0 and the Sharpe "zero volatility" check never fired.
    mean = math.fsum(values) / len(values)
    return math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1))


def annualized_volatility(returns: list[float], periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Standard deviation of returns scaled to a year (volatility grows with the square root of time)."""
    if len(returns) < 2:
        raise ValueError("need at least 2 returns")
    return _sample_std(returns) * math.sqrt(periods_per_year)


def rolling_volatility(returns: list[float], window: int, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> list[float | None]:
    """Annualized volatility over a sliding window; None until the window is full."""
    if window < 2:
        raise ValueError("window must be at least 2")
    result: list[float | None] = [None] * len(returns)
    for i in range(window - 1, len(returns)):
        result[i] = _sample_std(returns[i - window + 1: i + 1]) * math.sqrt(periods_per_year)
    return result


@dataclass(frozen=True)
class Drawdown:
    depth: float          # negative fraction, -0.25 means a 25% fall from the peak
    peak_index: int
    trough_index: int


def max_drawdown(prices: list[float]) -> Drawdown:
    """Largest peak-to-trough fall. The number investors feel most: 'how bad did it get?'"""
    _check_prices(prices)
    peak_index, worst = 0, 0.0
    worst_peak, worst_trough = 0, 0
    for i, price in enumerate(prices):
        if price > prices[peak_index]:
            peak_index = i
        drawdown = price / prices[peak_index] - 1
        if drawdown < worst:
            worst, worst_peak, worst_trough = drawdown, peak_index, i
    return Drawdown(worst, worst_peak, worst_trough)


def cagr(prices: list[float], periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Compound annual growth rate between the first and last price."""
    _check_prices(prices)
    years = (len(prices) - 1) / periods_per_year
    return (prices[-1] / prices[0]) ** (1 / years) - 1
```

**`app/services/stocks/risk_metrics.py`**

```python
# app/services/stocks/risk_metrics.py
#
# Purpose: risk numbers built on return series: Sharpe ratio, historical Value at Risk, beta
# against a benchmark (e.g. an index) and correlation. Pure Python. These describe the PAST
# and say nothing certain about the future; the UI and the explanation must say so.

import math

from app.services.stocks.indicators import TRADING_DAYS_PER_YEAR, _sample_std

MIN_OBSERVATIONS_FOR_VAR = 20


def sharpe_ratio(returns: list[float], risk_free_annual: float = 0.0, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float | None:
    """Average excess return per unit of volatility, annualised. None when volatility is zero."""
    if len(returns) < 2:
        raise ValueError("need at least 2 returns")
    excess = [r - risk_free_annual / periods_per_year for r in returns]
    std = _sample_std(excess)
    if std == 0:
        return None
    return (sum(excess) / len(excess)) / std * math.sqrt(periods_per_year)


def historical_var(returns: list[float], confidence: float = 0.95) -> float:
    """
    One-period historical Value at Risk as a positive loss fraction: "on 95% of days the loss
    was smaller than this". Uses the empirical percentile with linear interpolation.
    """
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    if len(returns) < MIN_OBSERVATIONS_FOR_VAR:
        raise ValueError(f"need at least {MIN_OBSERVATIONS_FOR_VAR} returns")
    ordered = sorted(returns)
    position = (1 - confidence) * (len(ordered) - 1)
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    percentile = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return max(0.0, -percentile)


def _covariance(a: list[float], b: list[float]) -> float:
    mean_a, mean_b = sum(a) / len(a), sum(b) / len(b)
    return sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b)) / (len(a) - 1)


def _check_pair(a: list[float], b: list[float]) -> None:
    if len(a) != len(b):
        raise ValueError("series must have the same length (align dates first)")
    if len(a) < 2:
        raise ValueError("need at least 2 observations")


def beta(asset_returns: list[float], benchmark_returns: list[float]) -> float:
    """Sensitivity to the benchmark: 1.2 means the asset tended to move 20% more than the market."""
    _check_pair(asset_returns, benchmark_returns)
    variance = _covariance(benchmark_returns, benchmark_returns)
    if variance == 0:
        raise ValueError("benchmark has zero variance")
    return _covariance(asset_returns, benchmark_returns) / variance


def correlation(a: list[float], b: list[float]) -> float:
    """Pearson correlation, -1 to 1."""
    _check_pair(a, b)
    denominator = _sample_std(a) * _sample_std(b)
    if denominator == 0:
        raise ValueError("a series has zero variance")
    return _covariance(a, b) / denominator
```

**`app/services/stocks/sentiment_aggregation.py`**

```python
# app/services/stocks/sentiment_aggregation.py
#
# Purpose: turn many per-headline sentiment results (from FinBERT or the fallback lexicon scorer)
# into ONE explainable score for a stock. Newer headlines count more (exponential half-life),
# and the result says whether it is RELIABLE (enough headlines) so a single headline never
# drives the story. Pure Python: the model itself lives elsewhere.

from dataclasses import dataclass

POSITIVE_ABOVE = 0.15
NEGATIVE_BELOW = -0.15
PROBABILITY_TOLERANCE = 0.01


@dataclass(frozen=True)
class HeadlineSentiment:
    positive: float
    negative: float
    neutral: float
    age_days: float          # how old the headline is

    def __post_init__(self) -> None:
        parts = (self.positive, self.negative, self.neutral)
        if any(p < 0 or p > 1 for p in parts) or abs(sum(parts) - 1) > PROBABILITY_TOLERANCE:
            raise ValueError("positive, negative and neutral must be probabilities that sum to 1")
        if self.age_days < 0:
            raise ValueError("age_days cannot be negative")

    @property
    def score(self) -> float:
        """Positive minus negative probability, from -1 (all bad news) to +1 (all good news)."""
        return self.positive - self.negative


@dataclass(frozen=True)
class AggregateSentiment:
    score: float             # weighted average, -1 to +1
    label: str               # 'positive' | 'neutral' | 'negative'
    n_items: int
    reliable: bool           # False when there are too few headlines to say anything
    effective_weight: float  # sum of recency weights, a measure of how much evidence there is


def aggregate_sentiment(items: list[HeadlineSentiment], half_life_days: float = 3.0, min_items: int = 3) -> AggregateSentiment:
    """A headline half_life_days old counts half as much as one from today."""
    if half_life_days <= 0 or min_items < 1:
        raise ValueError("half_life_days must be > 0 and min_items >= 1")
    if not items:
        return AggregateSentiment(0.0, "neutral", 0, False, 0.0)
    weights = [0.5 ** (item.age_days / half_life_days) for item in items]
    total = sum(weights)
    score = sum(w * item.score for w, item in zip(weights, items)) / total
    label = "positive" if score > POSITIVE_ABOVE else "negative" if score < NEGATIVE_BELOW else "neutral"
    return AggregateSentiment(round(score, 4), label, len(items), len(items) >= min_items, round(total, 4))
```

**`app/services/stocks/lexicon_sentiment.py`**

```python
# app/services/stocks/lexicon_sentiment.py
#
# Purpose: a tiny word-list headline scorer used as (1) a FALLBACK when FinBERT is not loaded
# (it needs PyTorch, which is heavy) and (2) a BASELINE to compare FinBERT against in the paper.
# Deliberately simple and transparent. Returns (positive, negative, neutral) probabilities.

import re

POSITIVE_WORDS = frozenset({
    "gain", "gains", "rise", "rises", "rose", "surge", "surges", "jump", "jumps", "profit", "profits",
    "growth", "beat", "beats", "record", "upgrade", "upgrades", "strong", "rally", "rallies", "outperform",
    "bullish", "recovery", "expands", "expansion", "dividend", "wins", "order", "approval",
})
NEGATIVE_WORDS = frozenset({
    "fall", "falls", "fell", "drop", "drops", "plunge", "plunges", "loss", "losses", "decline", "declines",
    "miss", "misses", "downgrade", "downgrades", "weak", "slump", "bearish", "fraud", "probe", "penalty",
    "default", "lawsuit", "cut", "cuts", "layoffs", "recall", "warning", "debt",
})
_NEGATIONS = frozenset({"not", "no", "never", "without", "fails", "failed"})
_WORD = re.compile(r"[a-z']+")


def score_headline(text: str) -> tuple[float, float, float]:
    """
    Counts positive and negative words; a negation just before a word flips it.
    neutral = 1 / (matches + 1), so a headline with no sentiment words is fully neutral.
    """
    words = _WORD.findall(text.lower())
    positive = negative = 0
    for i, word in enumerate(words):
        flipped = i > 0 and words[i - 1] in _NEGATIONS
        if word in POSITIVE_WORDS:
            negative, positive = (negative + 1, positive) if flipped else (negative, positive + 1)
        elif word in NEGATIVE_WORDS:
            positive, negative = (positive + 1, negative) if flipped else (positive, negative + 1)
    total = positive + negative
    return positive / (total + 1), negative / (total + 1), 1 / (total + 1)
```

**`app/services/stocks/walk_forward.py`**

```python
# app/services/stocks/walk_forward.py
#
# Purpose: honest evaluation of price-direction models. Time series must never be shuffled:
# a model may only be tested on data that comes AFTER everything it trained on (walk-forward /
# expanding window). Also gives the metrics and an exact binomial test so we can say whether a
# hit rate like 53% is distinguishable from a coin flip. Pure Python.

import math


def walk_forward_splits(n: int, initial_train: int, test_size: int = 1, step: int = 1) -> list[tuple[int, int, int]]:
    """
    Returns (train_end, test_start, test_end) index triples. Train = [0, train_end) and
    test = [test_start, test_end), with test_start == train_end, so no test row is ever
    used for training (no look-ahead leakage).
    """
    if initial_train < 1 or test_size < 1 or step < 1:
        raise ValueError("initial_train, test_size and step must be >= 1")
    splits: list[tuple[int, int, int]] = []
    train_end = initial_train
    while train_end + test_size <= n:
        splits.append((train_end, train_end, train_end + test_size))
        train_end += step
    return splits


def directional_accuracy(actual: list[float], predicted: list[float]) -> tuple[float, int]:
    """
    Share of periods where the predicted sign (up/down) matched the actual sign.
    Periods where the actual change is exactly zero are skipped. Returns (accuracy, n_used).
    """
    if len(actual) != len(predicted):
        raise ValueError("actual and predicted must have the same length")
    pairs = [(a, p) for a, p in zip(actual, predicted) if a != 0]
    if not pairs:
        raise ValueError("no non-zero actual changes to compare")
    hits = sum(1 for a, p in pairs if (a > 0) == (p > 0))
    return hits / len(pairs), len(pairs)


def binomial_p_value_greater(successes: int, n: int, p: float = 0.5) -> float:
    """
    One-sided exact probability of getting at least `successes` hits in n tries by luck alone
    (each try succeeding with probability p). Small values mean the hit rate is unlikely to be
    a coin flip. Uses log-gamma so large n does not overflow.
    """
    if not 0 <= successes <= n or n < 1 or not 0 < p < 1:
        raise ValueError("need 0 <= successes <= n, n >= 1 and 0 < p < 1")

    def log_pmf(k: int) -> float:
        return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
                + k * math.log(p) + (n - k) * math.log(1 - p))

    return min(1.0, sum(math.exp(log_pmf(k)) for k in range(successes, n + 1)))


def mean_absolute_error(actual: list[float], predicted: list[float]) -> float:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("need two non-empty series of the same length")
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def strategy_return(actual_returns: list[float], signals: list[int]) -> float:
    """
    Total return of a long/flat rule: hold (signal 1) or stay out (signal 0) each period.
    Compare with buy-and-hold (all signals 1) to see whether a model adds anything.
    """
    if len(actual_returns) != len(signals):
        raise ValueError("returns and signals must have the same length")
    growth = 1.0
    for r, s in zip(actual_returns, signals):
        growth *= 1 + (r if s else 0.0)
    return growth - 1
```

**`app/services/stocks/bootstrap_returns.py`**

```python
# app/services/stocks/bootstrap_returns.py
#
# Purpose: connect Phase 7 to Phase 6. Instead of assuming normally distributed returns, resample
# REAL historical monthly returns in blocks (block bootstrap keeps short-term streaks and
# crashes together) to project a SIP. This is the research follow-up noted in the Phase 6 spec:
# "Monte Carlo normal vs historical bootstrap". Pure Python, seeded, reproducible.

import random

from app.services.investing.monte_carlo import SimulationResult


def _percentile(sorted_values: list[float], q: float) -> float:
    position = q * (len(sorted_values) - 1)
    lower, upper = int(position), min(int(position) + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def block_bootstrap_sip(
    monthly_returns: list[float], monthly_sip: float, years: float, n_paths: int = 2000,
    block_length: int = 6, seed: int = 42, goal: float | None = None, annual_step_up: float = 0.0,
) -> SimulationResult:
    """
    monthly_returns: historical monthly returns as fractions (0.02 = +2%).
    Each path stitches random blocks of `block_length` consecutive months together until it
    covers the horizon. Nothing is invented: every simulated month really happened once.
    """
    if n_paths <= 0 or monthly_sip < 0 or years <= 0 or block_length < 1:
        raise ValueError("n_paths, years, block_length must be > 0 and monthly_sip >= 0")
    if len(monthly_returns) < block_length:
        raise ValueError("history is shorter than one block")
    rng = random.Random(seed)
    months = int(round(years * 12))
    max_start = len(monthly_returns) - block_length

    finals: list[float] = []
    for _ in range(n_paths):
        path: list[float] = []
        while len(path) < months:
            start = rng.randint(0, max_start)
            path.extend(monthly_returns[start:start + block_length])
        balance, contribution = 0.0, monthly_sip
        for month in range(1, months + 1):
            balance = balance * (1 + max(-0.99, path[month - 1])) + contribution
            if month % 12 == 0:
                contribution *= 1 + annual_step_up
        finals.append(balance)

    finals.sort()
    probability = None if goal is None else sum(1 for value in finals if value >= goal) / n_paths
    return SimulationResult(
        p10=round(_percentile(finals, 0.10), 2), p50=round(_percentile(finals, 0.50), 2),
        p90=round(_percentile(finals, 0.90), 2), probability_of_goal=probability, n_paths=n_paths,
    )
```


### Tests (copy into backend/tests/)

**`tests/test_indicators.py`**

```python
# tests/test_indicators.py

import math

import pytest

from app.services.stocks.indicators import (
    annualized_volatility, cagr, ema, log_returns, max_drawdown, rolling_volatility, rsi, simple_returns, sma,
)


def test_simple_returns_are_percentage_changes():
    assert simple_returns([100, 110, 99]) == pytest.approx([0.10, -0.10])


def test_log_returns_add_up_to_total_log_growth():
    prices = [100, 105, 98, 120]
    assert sum(log_returns(prices)) == pytest.approx(math.log(120 / 100))


def test_bad_prices_are_rejected():
    with pytest.raises(ValueError):
        simple_returns([100])
    with pytest.raises(ValueError):
        simple_returns([100, 0, 50])


def test_sma_values_and_leading_nones():
    result = sma([1, 2, 3, 4, 5], 3)
    assert result[:2] == [None, None] and result[2:] == pytest.approx([2, 3, 4])


def test_ema_of_a_constant_is_that_constant_and_starts_after_window():
    result = ema([5.0] * 10, 4)
    assert result[:3] == [None] * 3 and all(v == pytest.approx(5.0) for v in result[3:])


def test_ema_reacts_faster_than_sma_to_a_jump():
    values = [10.0] * 10 + [20.0] * 3
    assert ema(values, 5)[-1] > sma(values, 5)[-1]


def test_rsi_is_100_when_only_gains_and_0_when_only_losses():
    assert rsi([float(p) for p in range(1, 30)], 14)[-1] == 100.0
    assert rsi([float(p) for p in range(30, 1, -1)], 14)[-1] == pytest.approx(0.0)


def test_rsi_is_50_for_a_flat_series_and_stays_in_range():
    assert rsi([10.0] * 20, 14)[-1] == 50.0
    noisy = [100 + ((i * 7) % 11) - 5 for i in range(60)]
    assert all(0 <= v <= 100 for v in rsi([float(p) for p in noisy], 14) if v is not None)


def test_rsi_needs_enough_history():
    assert all(v is None for v in rsi([1.0, 2.0, 3.0], 14))


def test_constant_returns_have_zero_volatility():
    assert annualized_volatility([0.01] * 30) == pytest.approx(0.0, abs=1e-12)


def test_volatility_scales_with_sqrt_of_time():
    returns = [0.01, -0.02, 0.015, -0.005, 0.02, -0.01]
    daily_std = annualized_volatility(returns, 1)
    assert annualized_volatility(returns, 252) == pytest.approx(daily_std * math.sqrt(252))


def test_rolling_volatility_has_leading_nones():
    result = rolling_volatility([0.01, -0.01, 0.02, -0.02, 0.01], 3)
    assert result[:2] == [None, None] and all(v is not None for v in result[2:])


def test_max_drawdown_finds_the_worst_fall():
    result = max_drawdown([100, 120, 90, 110, 80, 130])
    assert result.depth == pytest.approx(80 / 120 - 1)
    assert (result.peak_index, result.trough_index) == (1, 4)


def test_rising_series_has_no_drawdown():
    assert max_drawdown([1, 2, 3, 4]).depth == 0


def test_cagr_doubling_in_one_year_is_100_percent():
    prices = [100.0] + [100.0] * 251 + [200.0]
    assert cagr(prices, 252) == pytest.approx(1.0)
```

**`tests/test_risk_metrics.py`**

```python
# tests/test_risk_metrics.py

import random

import pytest

from app.services.stocks.risk_metrics import beta, correlation, historical_var, sharpe_ratio


def test_sharpe_is_none_for_zero_volatility():
    assert sharpe_ratio([0.001] * 30) is None


def test_sharpe_is_positive_for_positive_drift_and_falls_with_risk_free_rate():
    rng = random.Random(1)
    returns = [0.001 + rng.gauss(0, 0.01) for _ in range(500)]
    assert sharpe_ratio(returns) > 0
    assert sharpe_ratio(returns, risk_free_annual=0.10) < sharpe_ratio(returns, risk_free_annual=0.0)


def test_var_is_a_positive_loss_near_the_empirical_percentile():
    returns = [i / 1000 for i in range(-50, 50)]    # -5% ... +4.9%
    var95 = historical_var(returns, 0.95)
    assert 0.04 < var95 < 0.05


def test_var_is_zero_when_there_are_no_losses_in_the_tail():
    assert historical_var([0.01] * 30) == 0.0


def test_higher_confidence_means_larger_var():
    rng = random.Random(2)
    returns = [rng.gauss(0, 0.02) for _ in range(500)]
    assert historical_var(returns, 0.99) > historical_var(returns, 0.90)


def test_var_needs_enough_observations():
    with pytest.raises(ValueError):
        historical_var([0.01] * 5)


def test_beta_of_the_benchmark_with_itself_is_one_and_doubling_gives_two():
    bench = [0.01, -0.02, 0.015, -0.01, 0.02, -0.005]
    assert beta(bench, bench) == pytest.approx(1.0)
    assert beta([2 * r for r in bench], bench) == pytest.approx(2.0)


def test_beta_of_an_independent_constant_is_zero():
    assert beta([0.01] * 6, [0.01, -0.02, 0.015, -0.01, 0.02, -0.005]) == pytest.approx(0.0, abs=1e-12)


def test_correlation_extremes():
    a = [0.01, -0.02, 0.015, -0.01, 0.02]
    assert correlation(a, a) == pytest.approx(1.0)
    assert correlation(a, [-x for x in a]) == pytest.approx(-1.0)


def test_misaligned_series_are_rejected():
    with pytest.raises(ValueError):
        beta([0.01, 0.02], [0.01])
```

**`tests/test_sentiment_aggregation.py`**

```python
# tests/test_sentiment_aggregation.py

import pytest

from app.services.stocks.lexicon_sentiment import score_headline
from app.services.stocks.sentiment_aggregation import HeadlineSentiment, aggregate_sentiment

GOOD = HeadlineSentiment(0.9, 0.05, 0.05, age_days=0)
BAD = HeadlineSentiment(0.05, 0.9, 0.05, age_days=0)


def test_score_is_positive_minus_negative():
    assert GOOD.score == pytest.approx(0.85)


def test_invalid_probabilities_are_rejected():
    with pytest.raises(ValueError):
        HeadlineSentiment(0.5, 0.5, 0.5, 0)
    with pytest.raises(ValueError):
        HeadlineSentiment(0.5, 0.3, 0.2, -1)


def test_no_headlines_is_neutral_and_unreliable():
    result = aggregate_sentiment([])
    assert result.label == "neutral" and not result.reliable and result.n_items == 0


def test_mostly_good_news_is_positive_and_reliable_with_enough_items():
    result = aggregate_sentiment([GOOD, GOOD, GOOD, BAD])
    assert result.label == "positive" and result.reliable


def test_two_headlines_are_not_reliable():
    assert not aggregate_sentiment([GOOD, GOOD]).reliable


def test_recent_news_outweighs_old_news():
    old_bad = HeadlineSentiment(0.05, 0.9, 0.05, age_days=30)
    new_good = HeadlineSentiment(0.9, 0.05, 0.05, age_days=0)
    assert aggregate_sentiment([old_bad, new_good]).score > 0.5


def test_half_life_halves_the_weight():
    items = [HeadlineSentiment(1, 0, 0, 3.0)]
    assert aggregate_sentiment(items, half_life_days=3.0).effective_weight == pytest.approx(0.5)


def test_lexicon_classifies_clear_headlines():
    pos, neg, neu = score_headline("Company profits surge as strong demand beats estimates")
    assert pos > neg and pos + neg + neu == pytest.approx(1.0)
    pos, neg, _ = score_headline("Shares plunge after fraud probe and downgrade")
    assert neg > pos


def test_lexicon_negation_flips_the_word():
    pos, neg, _ = score_headline("Company did not profit this quarter")
    assert neg > pos


def test_lexicon_without_sentiment_words_is_fully_neutral():
    assert score_headline("Board meeting scheduled for Tuesday") == (0.0, 0.0, 1.0)
```

**`tests/test_walk_forward.py`**

```python
# tests/test_walk_forward.py

import pytest

from app.services.stocks.walk_forward import (
    binomial_p_value_greater, directional_accuracy, mean_absolute_error, strategy_return, walk_forward_splits,
)


def test_splits_never_let_the_test_window_overlap_the_training_data():
    splits = walk_forward_splits(n=10, initial_train=5, test_size=2, step=1)
    assert splits[0] == (5, 5, 7)
    assert all(test_start == train_end and test_end <= 10 for train_end, test_start, test_end in splits)
    assert [s[0] for s in splits] == [5, 6, 7, 8]


def test_training_window_only_grows():
    ends = [s[0] for s in walk_forward_splits(30, 10, 1, 3)]
    assert ends == sorted(ends) and ends[0] == 10


def test_not_enough_data_gives_no_splits():
    assert walk_forward_splits(5, 5, 1) == []


def test_directional_accuracy_counts_sign_matches_and_skips_flat_days():
    accuracy, used = directional_accuracy([0.01, -0.02, 0.0, 0.03], [0.5, -0.1, 0.2, -0.4])
    assert used == 3 and accuracy == pytest.approx(2 / 3)


def test_directional_accuracy_rejects_bad_input():
    with pytest.raises(ValueError):
        directional_accuracy([0.01], [0.01, 0.02])
    with pytest.raises(ValueError):
        directional_accuracy([0.0, 0.0], [1, 1])


def test_binomial_p_value_known_cases():
    assert binomial_p_value_greater(0, 10) == pytest.approx(1.0)
    assert binomial_p_value_greater(10, 10) == pytest.approx(0.5 ** 10)
    assert binomial_p_value_greater(5, 10) == pytest.approx(0.623046875)   # P(X >= 5) for a fair coin


def test_53_percent_over_100_days_is_not_significant_but_65_percent_is():
    assert binomial_p_value_greater(53, 100) > 0.05
    assert binomial_p_value_greater(65, 100) < 0.01


def test_binomial_p_value_handles_large_n():
    assert 0 <= binomial_p_value_greater(2600, 5000) <= 1


def test_mae():
    assert mean_absolute_error([1, 2, 3], [2, 2, 5]) == pytest.approx(1.0)


def test_strategy_return_matches_buy_and_hold_when_always_in():
    returns = [0.1, -0.05, 0.02]
    assert strategy_return(returns, [1, 1, 1]) == pytest.approx(1.1 * 0.95 * 1.02 - 1)
    assert strategy_return(returns, [0, 0, 0]) == 0
    assert strategy_return(returns, [1, 0, 1]) == pytest.approx(1.1 * 1.02 - 1)
```

**`tests/test_bootstrap_returns.py`**

```python
# tests/test_bootstrap_returns.py

import pytest

from app.services.investing.sip_planner import future_value_sip
from app.services.stocks.bootstrap_returns import block_bootstrap_sip

HISTORY = [0.02, -0.03, 0.01, 0.04, -0.06, 0.03, 0.015, -0.01, 0.025, 0.0, -0.02, 0.05]


def test_same_seed_same_result():
    a = block_bootstrap_sip(HISTORY, 5000, 5, n_paths=200, seed=3)
    assert a == block_bootstrap_sip(HISTORY, 5000, 5, n_paths=200, seed=3)


def test_percentiles_are_ordered():
    r = block_bootstrap_sip(HISTORY, 5000, 5, n_paths=300)
    assert r.p10 <= r.p50 <= r.p90


def test_constant_history_matches_the_closed_form():
    monthly = (1.10) ** (1 / 12) - 1
    r = block_bootstrap_sip([monthly] * 24, 5000, 5, n_paths=20, block_length=6)
    assert r.p50 == pytest.approx(future_value_sip(5000, 0.10, 5), rel=1e-6)
    assert r.p10 == r.p90


def test_zero_returns_give_just_the_deposits():
    assert block_bootstrap_sip([0.0] * 12, 1000, 2, n_paths=10).p50 == pytest.approx(24000)


def test_goal_probability_falls_as_the_goal_rises():
    easy = block_bootstrap_sip(HISTORY, 5000, 5, n_paths=300, goal=100_000).probability_of_goal
    hard = block_bootstrap_sip(HISTORY, 5000, 5, n_paths=300, goal=2_000_000).probability_of_goal
    assert easy > hard


def test_history_shorter_than_one_block_is_rejected():
    with pytest.raises(ValueError):
        block_bootstrap_sip([0.01, 0.02], 1000, 2, block_length=6)
```


## 7. Code explanation (beginner level)

- **`indicators.py`**: `simple_returns` turns prices into daily percentage changes. `sma` and `ema` smooth prices; the EMA gives more weight to recent days (tested). `rsi` compares average gains with average losses using Wilder's smoothing, giving 0 to 100. Volatility is the standard deviation of returns, scaled by the square root of 252 trading days to give a yearly figure. `max_drawdown` walks through the prices remembering the highest peak so far and records the deepest fall from it. `cagr` is the yearly growth rate between the first and last price.
- **`risk_metrics.py`**: Sharpe ratio is average excess return divided by volatility. `historical_var` sorts past returns and reads the 5th percentile: "on 95% of days the loss was smaller than this". Beta is covariance with the benchmark divided by the benchmark's variance.
- **`sentiment_aggregation.py`**: each headline has positive, negative and neutral probabilities. Score is positive minus negative. Older headlines get a smaller weight (`0.5 ** (age / half_life)`). With fewer than 3 headlines the result is marked not reliable.
- **`lexicon_sentiment.py`**: counts positive and negative words, flipping a word that follows "not". It is a baseline, not a smart model, and says so.
- **`walk_forward.py`**: `walk_forward_splits` makes sure the test period is always after the training period (the rule that stops cheating by looking at the future). `binomial_p_value_greater` answers: "if the model were a coin flip, how likely is a hit rate this good?" The tests show that 53% over 100 days is NOT distinguishable from luck, but 65% is.
- **`bootstrap_returns.py`**: builds each simulated future by stitching together random blocks of REAL historical months. Blocks keep bad streaks together, which the normal-distribution Monte Carlo of Phase 6 cannot do.

## 8. Contracts for files still to build

### `market_data/base.py`
```python
@dataclass(frozen=True)
class PriceBar:  symbol: str; bar_date: date; open: Decimal; high: Decimal; low: Decimal; close: Decimal; volume: int | None
@dataclass(frozen=True)
class NewsItem:  symbol: str; headline: str; url: str; publisher: str | None; published_at: datetime

class MarketDataProvider(Protocol):
    name: str
    def get_daily_prices(self, symbol: str, start: date, end: date) -> list[PriceBar]: ...
    def get_news(self, symbol: str, limit: int) -> list[NewsItem]: ...
```
Providers raise one `ProviderError(kind="rate_limited" | "unavailable" | "not_found" | "bad_response")`. The cache service decides what to do for each kind; adapters never retry forever (max 2 retries with backoff, timeouts on every request).

### `cache_service.py`
`get_prices(db, symbol, days) -> PriceResult(bars, source, data_as_of, warnings)`. Follows the fallback chain in section 4. Writes with an upsert on (symbol, bar_date). Logs every provider call to `provider_calls`.

### `stock_service.py`
`analyze(db, user_id, symbol)`: load prices (needs at least 252 bars for the 200-day average, else a warning and fewer indicators, never a crash); benchmark bars; news and sentiment; compute everything with the pure modules; build `facts` with **pre-formatted number strings** (same lesson as Phase 6); call the explainer; store the analysis with `data_as_of`, sources and warnings.

### `stock_explainer.py`
One LLM call through the existing provider abstraction. Accept the text only if the numeric guard passes (percentages and numbers must appear in `facts`), the claims guard v3 passes, and no "lakh/crore/k" amount words appear. Otherwise return the template. The disclaimer is appended by code. Store `llm_used`, `explanation_faithful`, `claims_compliant`, `llm_calls`, `raw_prohibited_claims`.

## 9. Research plan (IEEE angle)

**Contribution (draft):** an explainable, fault-tolerant stock-analysis agent that separates deterministic analytics from LLM narration, with cache-and-fallback data handling, a guard against advice and prediction language, and an honest out-of-sample evaluation that reports negative results.

### Research questions and experiments
1. **RQ1 Sentiment model:** does FinBERT beat the word-list baseline on labelled financial sentences? Metrics: accuracy, macro-F1, confusion matrix, per-class recall. Dataset: Financial PhraseBank (Malo et al.), and FiQA sentiment as a second test. **Check each dataset's availability and licence before use; I have not verified them.** Models: lexicon, FinBERT, and optionally a TF-IDF plus logistic regression (scikit-learn) trained on a split.
2. **RQ2 Does sentiment lead returns?** Lead-lag test of aggregate sentiment against the next 1 and 5 day returns, using rank correlation with p-values, correcting for the number of stocks tested (Bonferroni). Expect weak or no effect, and report it as such.
3. **RQ3 Direction models:** next-day direction on walk-forward splits. Baselines: always up (buy-and-hold), previous day's direction, 5-day momentum sign, SMA crossover. Models: logistic regression and LightGBM on lagged returns, RSI, volatility, with and without sentiment features. Metrics: directional accuracy with the exact binomial test against 50%, strategy return against buy-and-hold with a transaction cost, and max drawdown. **Leakage controls:** features only from data up to day t; walk-forward only; scaling fitted on the training window only.
4. **RQ4 Historical bootstrap vs normal:** on the benchmark's monthly returns, compare the Phase 6 normal Monte Carlo with the block bootstrap for the same SIP: p10, p50, p90 and goal probability, over block lengths 3, 6, 12. This tests whether the normal assumption understates bad outcomes.
5. **RQ5 Robustness of the system:** simulate provider failure (rate limit, outage, bad response) and measure how many requests are still served by cache or fallback, plus freshness at the time of serving. Also: raw LLM prohibited-claims rate before guards, template fallback rate, LLM calls per analysis.

### Ablations
Sentiment features on or off; half-life 1, 3, 7 days; lexicon vs FinBERT in the pipeline; guard on or off; cache TTL 1, 6, 24 hours versus provider calls used.

### Known limitations to state
Daily free data may be delayed or limited in history; survivorship and selection bias if symbols are hand-picked; short sample periods; headlines from free sources may be incomplete; FinBERT was trained on English text and the Indian market has regional language news; price direction is very close to a random walk, so negative results are expected and valid; no transaction taxes or slippage beyond the simple cost used.

## 10. Implementation steps for Claude Code (gated prompts)

Stop after each prompt and report. Use Plan mode. Do not start the next one until the previous one passes. **No automated test may call the network.**

### Prompt 7A-0: provider spike (no app code)
```text
Read CLAUDE.md and docs/phase7-spec.md (sections 0, 5 and 8). Phase 6 is merged; confirm I am on a branch feature/vishesh-stocks cut from the updated DEV. Do NOT change the app yet. Write ONE throwaway script, backend/scripts/provider_spike.py, that tests market-data providers from MY machine and writes a findings table to docs/phase7-provider-spike.md. For each candidate provider that needs no new dependency or only the one listed: (1) Alpha Vantage via plain HTTPS (key from the ALPHAVANTAGE_API_KEY environment variable, never hardcoded, never printed): daily prices for three large Indian stocks plus an index proxy, trying both .BSE style and any documented NSE style symbols; (2) yfinance (install it only inside the container for this spike): the same symbols with the .NS suffix and the NSEI index symbol; (3) Google News style RSS for a headline query per stock, using only the standard library. For each, record: did it return data, how many daily bars and how many years of history, the latest date, how many headlines, any error or rate-limit message, and time taken. Be polite: at most 2 retries, a timeout on every request, a pause between calls, and stay far under the free limits. Do not store real price data in the repo; the findings file contains counts and dates only, no raw data. At the end give me a recommendation (primary provider, news source, whether yfinance is needed) and tell me exactly which environment variables I must set. Wait for my go-ahead before 7A-1.
```

### Prompt 7A-1: pure modules
```text
Create backend/app/services/stocks/ with the six modules from section 6 of docs/phase7-spec.md and backend/tests/ with the five test files, copied EXACTLY, plus the empty __init__.py. Run: docker compose exec backend python -m pytest tests -q. Expect 51 new tests to pass on top of the existing ones. Do not change logic. Commit and report totals.
```

### Prompt 7A-2: data model
```text
Add models/stock.py (WatchlistItem, PriceBar, NewsItemRow, StockAnalysis, ProviderCall) and schemas/stock.py as described in section 4 of the spec. Alembic migration 0006_stocks.py (down_revision 0005). Numeric(14,4) for prices. The unique constraints and the (user_id, created_at DESC) index from the spec. Money/price columns never Float. Test that the migration applies on a clean database, downgrades to 0005, and upgrades again. Literal types in schemas for fixed values; a strict symbol validator (letters, digits, dot, dash, caret, max 20 characters). Report.
```

### Prompt 7A-3: providers, cache, fallback
```text
Using the recommendation in docs/phase7-provider-spike.md, create market_data/ (base.py, the approved provider adapter(s), rss_news_provider.py if chosen, factory.py, cache_service.py) per section 8 of the spec. Add MARKET_DATA_PROVIDER, NEWS_PROVIDER, ALPHAVANTAGE_API_KEY (if used), STOCK_CACHE_TTL_HOURS, STOCK_WATCHLIST_LIMIT to core/config.py and backend/.env.example with comments. Any new package goes into requirements.txt only if the spike proved it is needed, with a comment explaining why it is outside the original approved stack and that I approved it on 2026-11-09. Adapters must use timeouts, at most 2 retries with backoff, and raise ProviderError with a kind. cache_service must implement the fallback chain exactly and log provider_calls. Write tests with a FakeProvider and recorded sample responses stored as small synthetic fixtures: fresh hit, miss then fetch, rate-limited then stale cache with the data_stale warning, nothing cached gives a clear error, upsert does not duplicate. No test may use the network. Then write scripts/seed_market_cache.py that fills the cache for a default list of symbols and benchmark while online. Report.
```

### Prompt 7A-4: sentiment scorers
```text
Create sentiment/ (base.py protocol, lexicon_scorer.py wrapping services/stocks/lexicon_sentiment.py, finbert_scorer.py). FinBERT: load lazily on first use through HuggingFace transformers, model name from settings (default ProsusAI/finbert; verify on my machine that it downloads and that its licence allows academic use, and tell me), run on CPU, batch size from settings, and return positive/negative/neutral probabilities in the same shape as the lexicon. It must NOT be imported at app start, must NOT be in the default requirements.txt, and the app must start and pass all tests without torch installed. Put torch (CPU wheel) and transformers in backend/requirements-ml.txt and add an optional Docker Compose profile "ml" that builds an image from it. SENTIMENT_SCORER=lexicon|finbert in settings; if finbert is chosen but unavailable, fall back to lexicon and add the warning "finbert_unavailable". Tests with a stub scorer; the FinBERT test is skipped when transformers is not installed. Add POST /api/stocks/sentiment/score in the routes step. Report image sizes and first-load time if you build the ml profile.
```

### Prompt 7A-5: analysis service, explainer, routes
```text
Create services/stock_service.py, agents/stock_explainer.py and api/routes/stocks.py per sections 4 and 8. Extend the claims guard with the v3 patterns from section 5.9 (advice and prediction wording) as a separate function so the Phase 6 guard stays unchanged, and write tests in BOTH directions: advice and prediction sentences are rejected; neutral descriptions like "the price fell 4% over the last month" and "compared with a buy-and-hold approach" pass. facts use pre-formatted number strings; the explainer rejects any number or percentage not in facts. Store llm_used, explanation_faithful, claims_compliant, llm_calls, raw_prohibited_claims, data_as_of, sources and warnings. Fewer than 252 bars must give fewer indicators and a warning, never a crash. Route tests: auth, user isolation, symbol validation, watchlist limit, disclaimer present, status endpoint. Extend scripts/smoke_test.py with one step that works from the seeded cache with no internet. Run the full suite and the smoke test, commit, report totals. Do not push.
```

### Prompt 7B: evaluation and docs
```text
Write evaluation scripts in the repo's flat style (evaluation/evaluate_stocks.py plus helpers) for RQ1 to RQ5 in section 9, writing stocks_metrics.csv/.md and docs/modules/stocks-evaluation.md. FIRST commit a rules-style file stating the hypotheses, the metrics and the baselines BEFORE writing any model code (same pre-registration habit as Phase 6B). Use only approved libraries (scikit-learn, LightGBM). Walk-forward only, with the leakage controls listed in the spec. For RQ1 tell me which labelled dataset you can actually obtain and its licence; if none is reachable, evaluate on a small clearly labelled synthetic set and say so. Report negative results honestly. Keep a "Changes after seeing results" table, with at most one round of fixes. Update docs/modules (stocks.md, api.md, database.md, testing.md, architecture.md) and the CLAUDE.md status. Report the key numbers.
```

### Prompt 7C: frontend
```text
Add frontend/app/(console)/stocks/page.tsx in the existing console style (Tailwind, Shadcn UI, Recharts only) and a link in components/nav-bar.tsx. Sections: (1) watchlist add/remove with last data date; (2) refresh button with a clear freshness badge showing source and "as of" date, and the data_stale warning in plain words; (3) price chart with 50 and 200 day averages; (4) analysis card: trend, RSI with its convention label, volatility, max drawdown, VaR, beta; (5) sentiment card with the headlines list, aggregate score, and a "not enough headlines" notice when unreliable; (6) the explanation with an AI-written / Template badge; (7) a headline scorer box calling POST /sentiment/score, showing the three probabilities for lexicon and FinBERT when available; (8) CSV upload fallback; (9) status panel. Permanent banner: "Educational analysis tool, not financial advice. Past data does not predict future prices." Types for every response, NEXT_PUBLIC_API_URL only, Indian rupee formatting where relevant, empty and loading and error states, responsive. npm run lint and npm run build must pass. Do not push.
```

### Prompt 7D: connect to Phase 6
```text
Add an optional method="bootstrap" to POST /api/investing/simulate (default stays "normal"). It uses block_bootstrap_sip with the benchmark's monthly returns built from cached price_bars, shows the history length and date range used, and falls back to the normal method with a warning "history_too_short" when there are fewer than 60 monthly returns. Add a normal-vs-bootstrap toggle to the what-if simulator on /investing. Tests with synthetic fixtures. Report.
```

## 11. Common beginner mistakes
- Calling the live API inside tests. Tests become slow, flaky and eat the free quota.
- Not caching. A 25-request daily limit disappears in minutes of testing.
- Committing API keys or downloaded price data.
- Shuffling time series before splitting. The model then "sees the future" and looks brilliant.
- Fitting a scaler on all the data before the split (a quiet form of leakage).
- Calling a 53% hit rate "a good model" without a significance test.
- Using adjusted and unadjusted prices together, which makes fake jumps at splits and dividends.
- Trusting one headline. Always show how many headlines the sentiment is based on.
- Writing "strong buy" in the UI. Use facts only.
- Loading FinBERT at app start. It slows startup and needs heavy packages; load it lazily and optionally.

## 12. Testing steps (when everything is built)
1. `docker compose exec backend python -m pytest tests -q` (all green, no network used).
2. `docker compose exec backend python -m scripts.smoke_test` (the stocks step works from the seeded cache).
3. `docker compose exec backend python -m scripts.seed_market_cache` while online, then try the app with Wi-Fi off. It must still show analyses with the `data_stale` or cache-source badge.
4. In the app: add a stock, refresh, generate an analysis, check the freshness badge, try the headline scorer with "Profit surges on record orders" and "Shares plunge after fraud probe".
5. `docker compose exec backend python -m evaluation.evaluate_stocks` and read `docs/modules/stocks-evaluation.md`.

## 13. Next steps
Phase 8 (Goal Planning) reuses `required_sip`, `months_to_reach`, `simulate_sip` and the new bootstrap to give goal-by-goal plans. Phase 9 (Automation) puts the daily data refresh and alerts on a Celery schedule, which replaces the manual refresh button.
