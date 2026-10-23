# app/services/investing/monte_carlo.py
#
# Purpose: show a RANGE of outcomes instead of one number. Simulates many possible return
# paths for a SIP and reports the 10th/50th/90th percentile corpus and the chance of
# reaching a goal. Seeded so results are reproducible (needed for tests and the paper).
# Pure Python (random + math). The return/volatility inputs are assumptions supplied by the
# caller, never invented here.

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class SimulationResult:
    p10: float
    p50: float
    p90: float
    probability_of_goal: float | None   # None when no goal was given
    n_paths: int


def _percentile(sorted_values: list[float], q: float) -> float:
    position = q * (len(sorted_values) - 1)
    lower, upper = int(position), min(int(position) + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def simulate_sip(
    monthly_sip: float, years: float, mean_annual_return: float, annual_volatility: float,
    n_paths: int = 2000, seed: int = 42, goal: float | None = None, annual_step_up: float = 0.0,
) -> SimulationResult:
    """Monthly returns are normal with the given mean and volatility (volatility / sqrt(12))."""
    if n_paths <= 0 or annual_volatility < 0 or monthly_sip < 0:
        raise ValueError("n_paths must be > 0; volatility and SIP must be >= 0")
    rng = random.Random(seed)
    mean_monthly = (1 + mean_annual_return) ** (1 / 12) - 1
    sigma_monthly = annual_volatility / math.sqrt(12)
    months = int(round(years * 12))

    finals: list[float] = []
    for _ in range(n_paths):
        balance, contribution = 0.0, monthly_sip
        for month in range(1, months + 1):
            monthly_return = max(-0.99, rng.gauss(mean_monthly, sigma_monthly))   # a month cannot lose more than 99%
            balance = balance * (1 + monthly_return) + contribution
            if month % 12 == 0:
                contribution *= 1 + annual_step_up
        finals.append(balance)

    finals.sort()
    probability = None if goal is None else sum(1 for value in finals if value >= goal) / n_paths
    return SimulationResult(
        p10=round(_percentile(finals, 0.10), 2), p50=round(_percentile(finals, 0.50), 2),
        p90=round(_percentile(finals, 0.90), 2), probability_of_goal=probability, n_paths=n_paths,
    )
