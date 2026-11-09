# backend/evaluation/stocks_leadlag.py
#
# Purpose: RQ2 helpers. Does aggregated news sentiment LEAD returns? Assigns each headline to the
# trading day whose close it was known at (leakage control), aggregates sentiment per day with the
# app's half-life weighting, and tests the rank correlation with the NEXT 1- and 5-day returns using a
# permutation p-value. Also the pre-registered synthetic POWER analysis: how many days of headlines
# would be needed to detect a lead of a given size?
#
# Definitions (stocks_rules.py RQ2_*):
#   cutoff(d)      16:00 IST on trading day d (the app's "close is final" time)
#   assignment     a headline belongs to the first trading day d with cutoff(d) >= published_at, so
#                  news after the close (or on a holiday) counts for the NEXT trading day
#   sentiment day  a trading day with at least one headline assigned to it (new information that day);
#                  its value aggregates every headline assigned in the RQ2_LOOKBACK_DAYS calendar days
#                  up to and including d, ages measured from cutoff(d)
#   forward return close[d + h] / close[d] - 1, h trading days ahead
#
# Pure Python (random, math, bisect): no third-party statistics package needed.

import bisect
import math
import random
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone

from app.market_data.base import CLOSE_FINAL_AFTER_IST, IST
from app.services.stocks.sentiment_aggregation import HeadlineSentiment, aggregate_sentiment
from evaluation.stocks_rules import RQ2_LOOKBACK_DAYS, SEED


def cutoff(day: date) -> datetime:
    return datetime.combine(day, CLOSE_FINAL_AFTER_IST, tzinfo=IST).astimezone(timezone.utc)


def assign_days(published: Sequence[datetime], trading_days: Sequence[date]) -> list[date | None]:
    """Trading day each headline was known by (None if after the last trading day)."""
    cutoffs = [cutoff(d) for d in trading_days]
    out = []
    for moment in published:
        i = bisect.bisect_left(cutoffs, moment)
        out.append(trading_days[i] if i < len(trading_days) else None)
    return out


def daily_sentiment(published: Sequence[datetime], probabilities: Sequence[tuple[float, float, float]],
                    trading_days: Sequence[date], half_life_days: float,
                    lookback_days: int = RQ2_LOOKBACK_DAYS) -> dict[date, float]:
    """Sentiment score for every sentiment day (see the header). probabilities: (pos, neg, neutral)."""
    assigned = assign_days(published, trading_days)
    by_day: dict[date, list[int]] = {}
    for i, day in enumerate(assigned):
        if day is not None:
            by_day.setdefault(day, []).append(i)
    out: dict[date, float] = {}
    for day in sorted(by_day):
        start = day - timedelta(days=lookback_days - 1)
        members = [i for i, d in enumerate(assigned) if d is not None and start <= d <= day]
        items = []
        for i in members:
            age = max(0.0, (cutoff(day) - published[i]).total_seconds() / 86400)
            pos, neg, neu = probabilities[i]
            items.append(HeadlineSentiment(pos, neg, neu, age))
        out[day] = aggregate_sentiment(items, half_life_days, min_items=1).score
    return out


def forward_returns(trading_days: Sequence[date], closes: Sequence[float], horizon: int) -> dict[date, float]:
    return {trading_days[i]: closes[i + horizon] / closes[i] - 1 for i in range(len(closes) - horizon)}


def ranks(values: Sequence[float]) -> list[float]:
    """Average ranks (ties share the mean of their positions), 1-based."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    ma, mb = math.fsum(a) / n, math.fsum(b) / n
    cov = math.fsum((x - ma) * (y - mb) for x, y in zip(a, b))
    va, vb = math.fsum((x - ma) ** 2 for x in a), math.fsum((y - mb) ** 2 for y in b)
    if va == 0 or vb == 0:
        return 0.0
    return cov / math.sqrt(va * vb)


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or len(a) < 3:
        raise ValueError("need two series of the same length, at least 3")
    return _pearson(ranks(a), ranks(b))


def permutation_p(a: Sequence[float], b: Sequence[float], permutations: int, seed: int = SEED) -> float:
    """Two-sided: share of shuffles of b with |rho| at least the observed |rho| (+1 smoothing)."""
    observed = abs(spearman(a, b))
    ra, rb = ranks(a), list(ranks(b))
    rng = random.Random(seed)
    extreme = 0
    for _ in range(permutations):
        rng.shuffle(rb)
        if abs(_pearson(ra, rb)) >= observed - 1e-12:
            extreme += 1
    return (extreme + 1) / (permutations + 1)


def normal_two_sided_p(rho: float, n: int) -> float:
    """Large-sample p for Spearman's rho under no association: z = rho * sqrt(n - 1)."""
    z = abs(rho) * math.sqrt(n - 1)
    return math.erfc(z / math.sqrt(2))


def power(rho: float, n: int, alpha: float, replications: int, seed: int = SEED) -> float:
    """Share of synthetic samples (bivariate normal, correlation rho) where the test rejects at alpha."""
    rng = random.Random(seed)
    noise = math.sqrt(1 - rho * rho)
    rejected = 0
    for _ in range(replications):
        x = [rng.gauss(0, 1) for _ in range(n)]
        y = [rho * xi + noise * rng.gauss(0, 1) for xi in x]
        if normal_two_sided_p(spearman(x, y), n) < alpha:
            rejected += 1
    return rejected / replications
