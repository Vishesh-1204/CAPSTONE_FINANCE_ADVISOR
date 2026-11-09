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
