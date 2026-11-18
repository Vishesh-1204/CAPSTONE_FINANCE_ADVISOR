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
