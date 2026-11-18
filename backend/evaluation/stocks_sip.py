# backend/evaluation/stocks_sip.py
#
# Purpose: RQ4 helpers. Does a historical BLOCK BOOTSTRAP change a SIP projection compared with the
# Phase 6 NORMAL Monte Carlo? Builds month-end returns of the benchmark, runs both methods with
# MATCHED mean and standard deviation (so only the shape of the distribution differs), and gives the
# descriptive extras: skewness, excess kurtosis and the realised outcome of every historical 5-year SIP.
#
# Both simulators are the app's own: services/investing/monte_carlo.simulate_sip (normal) and
# services/stocks/bootstrap_returns.block_bootstrap_sip (bootstrap). Pure Python.

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from app.services.investing.monte_carlo import SimulationResult, simulate_sip
from app.services.stocks.bootstrap_returns import block_bootstrap_sip


def month_end_closes(dates: Sequence[date], closes: Sequence[float]) -> list[tuple[str, float]]:
    """('YYYY-MM', close on the month's last trading day) for every month, oldest first."""
    out: dict[str, float] = {}
    for day, close in zip(dates, closes):
        out[f"{day.year:04d}-{day.month:02d}"] = close        # later days overwrite: the last one wins
    return list(out.items())


def monthly_returns(month_closes: Sequence[tuple[str, float]]) -> list[float]:
    values = [c for _, c in month_closes]
    return [values[i] / values[i - 1] - 1 for i in range(1, len(values))]


def matched_normal_inputs(returns: Sequence[float]) -> tuple[float, float]:
    """(mean_annual_return, annual_volatility) that make simulate_sip use the sample's monthly mean and sd."""
    n = len(returns)
    mean = math.fsum(returns) / n
    sd = math.sqrt(math.fsum((r - mean) ** 2 for r in returns) / (n - 1))
    return (1 + mean) ** 12 - 1, sd * math.sqrt(12)


def shape(returns: Sequence[float]) -> tuple[float, float]:
    """(skewness, excess kurtosis) of the monthly returns (population moments)."""
    n = len(returns)
    mean = math.fsum(returns) / n
    m2 = math.fsum((r - mean) ** 2 for r in returns) / n
    m3 = math.fsum((r - mean) ** 3 for r in returns) / n
    m4 = math.fsum((r - mean) ** 4 for r in returns) / n
    return m3 / m2 ** 1.5, m4 / m2 ** 2 - 3


def goal_value(monthly_sip: float, years: int, annual_return: float) -> float:
    """Future value of the SIP when every month earns (1 + annual_return) ** (1/12) - 1 (same timing as the simulators)."""
    i = (1 + annual_return) ** (1 / 12) - 1
    months = years * 12
    return monthly_sip * ((1 + i) ** months - 1) / i


def realised_sip(returns: Sequence[float], monthly_sip: float) -> float:
    """Final balance of a SIP that lived through exactly these monthly returns (simulators' timing)."""
    balance = 0.0
    for r in returns:
        balance = balance * (1 + max(-0.99, r)) + monthly_sip
    return balance


def rolling_realised(returns: Sequence[float], monthly_sip: float, years: int) -> list[float]:
    months = years * 12
    return [realised_sip(returns[s:s + months], monthly_sip) for s in range(len(returns) - months + 1)]


@dataclass(frozen=True)
class SipComparison:
    years: int
    method: str                  # "normal" | "bootstrap_b3" | "bootstrap_b6" | "bootstrap_b12"
    result: SimulationResult
    goal: float


def compare(returns: Sequence[float], monthly_sip: float, years_list: Sequence[int], blocks: Sequence[int],
            paths: int, seed: int, goal_annual_return: float) -> list[SipComparison]:
    mean_annual, volatility = matched_normal_inputs(returns)
    out = []
    for years in years_list:
        goal = goal_value(monthly_sip, years, goal_annual_return)
        out.append(SipComparison(years, "normal", simulate_sip(
            monthly_sip, years, mean_annual, volatility, n_paths=paths, seed=seed, goal=goal), goal))
        for block in blocks:
            out.append(SipComparison(years, f"bootstrap_b{block}", block_bootstrap_sip(
                list(returns), monthly_sip, years, n_paths=paths, block_length=block, seed=seed, goal=goal), goal))
    return out
