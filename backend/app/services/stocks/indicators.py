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
