# app/services/stocks/bootstrap_returns.py
#
# Purpose: connect Phase 7 to Phase 6. Instead of assuming normally distributed returns, resample
# REAL historical monthly returns in blocks (block bootstrap keeps short-term streaks and
# crashes together) to project a SIP. This is the research follow-up noted in the Phase 6 spec:
# "Monte Carlo normal vs historical bootstrap". Pure Python, seeded, reproducible.

#
# Phase 7D adds the two helpers the what-if simulator needs (POST /api/investing/simulate with
# method="bootstrap"):
#   * monthly_history:   daily closes -> month-end closes -> monthly returns (complete months only,
#                        newest unbroken run of calendar months)
#   * match_moments:     shift and stretch those returns so their mean and standard deviation equal
#                        the portfolio's assumed ones. The benchmark is an EQUITY index; without this a
#                        conservative portfolio would get the same range as an all-equity one. After it,
#                        normal vs bootstrap differ only in the SHAPE of the distribution (fat tails,
#                        streaks kept together in blocks), the same comparison as RQ4.

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.services.investing.monte_carlo import SimulationResult


@dataclass(frozen=True)
class MonthlyHistory:
    """Monthly returns built from daily closes. returns[i] runs from close_dates[i] to close_dates[i + 1]."""
    close_dates: list[date]        # the last trading day of each month used, oldest first
    returns: list[float]           # len(close_dates) - 1 values (fractions, 0.02 = +2%)

    @property
    def start(self) -> date | None:
        return self.close_dates[0] if self.returns else None

    @property
    def end(self) -> date | None:
        return self.close_dates[-1] if self.returns else None


def _month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def monthly_history(dates: Sequence[date], closes: Sequence[float], today: date) -> MonthlyHistory:
    """
    dates/closes: daily bars, oldest first. Keeps the LAST close of each calendar month, and:
      * drops the month of `today` (it is not finished, so its "month-end" close is not one yet);
      * keeps only the newest unbroken run of calendar months, so a gap in the cache (say a CSV with a
        missing year) never turns into one fake "monthly" return that spans many months.
    """
    if len(dates) != len(closes):
        raise ValueError("dates and closes must have the same length")
    last_close: dict[int, tuple[date, float]] = {}
    for day, close in zip(dates, closes):
        if _month_index(day) < _month_index(today):
            last_close[_month_index(day)] = (day, float(close))     # later days overwrite: the last one wins
    months = sorted(last_close)
    if not months:
        return MonthlyHistory([], [])
    first = len(months) - 1
    while first > 0 and months[first - 1] == months[first] - 1:
        first -= 1
    run = [last_close[m] for m in months[first:]]
    returns = [run[i][1] / run[i - 1][1] - 1 for i in range(1, len(run))]
    return MonthlyHistory([d for d, _ in run], returns)


def mean_and_sd(values: Sequence[float]) -> tuple[float, float]:
    """Sample mean and sample standard deviation (n - 1). Needs at least two values."""
    if len(values) < 2:
        raise ValueError("need at least two values")
    mean = math.fsum(values) / len(values)
    return mean, math.sqrt(math.fsum((v - mean) ** 2 for v in values) / (len(values) - 1))


def annualised(monthly_mean: float, monthly_sd: float) -> tuple[float, float]:
    """(annual return, annual volatility) with the SAME conversion simulate_sip uses, in reverse."""
    return (1 + monthly_mean) ** 12 - 1, monthly_sd * math.sqrt(12)


def match_moments(returns: Sequence[float], annual_return: float, annual_volatility: float) -> list[float]:
    """
    Rescale returns to the monthly mean and sd that simulate_sip would use for these annual inputs:
    r' = target_mean + (r - sample_mean) * target_sd / sample_sd. Order, ranks and shape are kept
    (a crash month stays the worst month), only the location and the spread change.
    """
    if annual_volatility < 0:
        raise ValueError("annual_volatility must be >= 0")
    target_mean = (1 + annual_return) ** (1 / 12) - 1
    target_sd = annual_volatility / math.sqrt(12)
    mean, sd = mean_and_sd(returns)
    if sd == 0:                                    # a flat history has no shape to keep
        return [target_mean] * len(returns)
    return [target_mean + (r - mean) * target_sd / sd for r in returns]


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
