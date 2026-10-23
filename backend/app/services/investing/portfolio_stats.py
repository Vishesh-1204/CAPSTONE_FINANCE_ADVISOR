# app/services/investing/portfolio_stats.py
#
# Purpose: combine per-asset-class assumptions (expected return, volatility) and a split into
# one portfolio-level return and volatility, so the Monte Carlo can simulate the whole plan.
# All numbers are ASSUMPTIONS supplied by configuration (never hard-coded in logic) and are
# shown to the user as assumptions. Pure Python.

import math
from dataclasses import dataclass

from app.services.investing.allocation import ASSET_CLASSES


@dataclass(frozen=True)
class AssetAssumption:
    expected_return: float   # annual, 0.10 = 10%
    volatility: float        # annual standard deviation


# Pairwise correlation assumptions between asset classes (symmetric). Missing pairs mean 0.
DEFAULT_CORRELATIONS: dict[frozenset, float] = {
    frozenset(("equity", "debt")): 0.10,
    frozenset(("equity", "gold")): 0.05,
    frozenset(("equity", "cash")): 0.00,
    frozenset(("debt", "gold")): 0.10,
    frozenset(("debt", "cash")): 0.30,
    frozenset(("gold", "cash")): 0.00,
}


def _correlation(a: str, b: str, correlations: dict[frozenset, float]) -> float:
    return 1.0 if a == b else correlations.get(frozenset((a, b)), 0.0)


def portfolio_return_and_volatility(
    weights: dict[str, float],
    assumptions: dict[str, AssetAssumption],
    correlations: dict[frozenset, float] | None = None,
) -> tuple[float, float]:
    """Weighted mean return, and volatility = sqrt(sum_i sum_j w_i w_j sigma_i sigma_j rho_ij)."""
    correlations = DEFAULT_CORRELATIONS if correlations is None else correlations
    missing = [name for name in ASSET_CLASSES if weights.get(name, 0.0) > 0 and name not in assumptions]
    if missing:
        raise ValueError(f"no assumptions for: {', '.join(missing)}")
    mean = sum(weights.get(n, 0.0) * assumptions[n].expected_return for n in ASSET_CLASSES if n in assumptions)
    variance = 0.0
    for a in ASSET_CLASSES:
        for b in ASSET_CLASSES:
            if a in assumptions and b in assumptions:
                variance += (weights.get(a, 0.0) * weights.get(b, 0.0)
                             * assumptions[a].volatility * assumptions[b].volatility
                             * _correlation(a, b, correlations))
    return mean, math.sqrt(max(variance, 0.0))
