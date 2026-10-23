# app/services/investing/allocation.py
#
# Purpose: turn a risk category plus an investing horizon into a target split across asset
# classes, and compare it with what the user actually holds (drift -> rebalancing actions).
# The numbers are ILLUSTRATIVE DESIGN CHOICES for an educational tool, not market facts or
# advice. They live in an `AllocationPolicy` so they can be changed in one place and ablated.

from dataclasses import dataclass, field

ASSET_CLASSES = ("equity", "debt", "gold", "cash")


@dataclass(frozen=True)
class AllocationPolicy:
    base: dict[str, dict[str, float]] = field(default_factory=lambda: {
        "conservative": {"equity": 0.25, "debt": 0.55, "gold": 0.10, "cash": 0.10},
        "moderate":     {"equity": 0.55, "debt": 0.30, "gold": 0.10, "cash": 0.05},
        "aggressive":   {"equity": 0.75, "debt": 0.12, "gold": 0.08, "cash": 0.05},
    })
    # (horizon below this many years, maximum equity share). Short horizons cannot ride out a crash.
    equity_cap_by_horizon: tuple[tuple[float, float], ...] = (
        (1.0, 0.0), (3.0, 0.20), (5.0, 0.40), (7.0, 0.60),
    )
    drift_threshold: float = 0.05     # rebalance when a class is 5 percentage points off target


DEFAULT_POLICY = AllocationPolicy()


def equity_cap(horizon_years: float, policy: AllocationPolicy = DEFAULT_POLICY) -> float:
    """Highest equity share allowed for this horizon (1.0 means no cap)."""
    for limit_years, cap in policy.equity_cap_by_horizon:
        if horizon_years < limit_years:
            return cap
    return 1.0


def target_allocation(category: str, horizon_years: float, policy: AllocationPolicy = DEFAULT_POLICY) -> dict[str, float]:
    """
    Base split for the category, then any equity above the horizon cap moves to debt
    (or to cash when the horizon is under a year). Always sums to exactly 1.0.
    """
    if category not in policy.base:
        raise ValueError(f"unknown risk category: {category}")
    weights = dict(policy.base[category])
    cap = equity_cap(horizon_years, policy)
    if weights["equity"] > cap:
        excess = weights["equity"] - cap
        weights["equity"] = cap
        destination = "cash" if horizon_years < 1.0 else "debt"
        weights[destination] += excess
    rounded = {name: round(weight, 4) for name, weight in weights.items()}
    rounded["debt"] = round(rounded["debt"] + (1.0 - sum(rounded.values())), 4)   # absorb rounding dust
    return rounded


@dataclass(frozen=True)
class DriftRow:
    asset_class: str
    current_weight: float
    target_weight: float
    amount_change: float      # rupees to add (+) or reduce (-) to hit the target at the same total
    action: str               # 'add' | 'reduce' | 'hold'


def rebalance_actions(
    holdings: dict[str, float], target: dict[str, float], policy: AllocationPolicy = DEFAULT_POLICY
) -> list[DriftRow]:
    """
    Compares current holdings (rupees per asset class) with the target split.
    A class is only flagged when it is off by more than the drift threshold, which stops
    the advisor from nagging about tiny differences.
    """
    total = sum(holdings.get(name, 0.0) for name in ASSET_CLASSES)
    rows: list[DriftRow] = []
    for name in ASSET_CLASSES:
        current = holdings.get(name, 0.0)
        current_weight = current / total if total > 0 else 0.0
        target_weight = target.get(name, 0.0)
        change = round(total * target_weight - current, 2)
        flagged = total > 0 and abs(current_weight - target_weight) > policy.drift_threshold
        action = "hold" if not flagged else ("add" if change > 0 else "reduce")
        rows.append(DriftRow(name, round(current_weight, 4), target_weight, change, action))
    return rows
