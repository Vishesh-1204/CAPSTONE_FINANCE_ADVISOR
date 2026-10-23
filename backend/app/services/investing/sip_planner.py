# app/services/investing/sip_planner.py
#
# Purpose: size a monthly SIP so it is AFFORDABLE even in a bad month, and show what it could
# grow to under clearly stated ASSUMED returns. "Affordable" uses the LOWER bound of the
# Phase 5 forecast surplus, not the average, so one weak month does not break the plan.
# Projections are scenarios under assumptions, never promises. Pure Python.

from dataclasses import dataclass


@dataclass(frozen=True)
class SipRecommendation:
    capacity_total: float          # most the user can safely invest per month
    recommended_total: float       # what we suggest investing per month (<= capacity_total)
    change_vs_existing: float      # +increase / -decrease versus current SIPs
    emergency_topup: float         # part of the surplus reserved to fill the emergency fund
    limited_by: str                # 'capacity' | 'target' | 'none'
    # True when the surplus is smaller than the top-up the fill period needs: all of the
    # surplus goes to the emergency fund, which then takes longer than months_to_fill to fill
    emergency_topup_capped: bool = False


def recommend_sip(
    surplus_before_investing_low: float, existing_sip: float, emergency_gap: float,
    months_to_fill_emergency: int = 12, safety_factor: float = 0.8, target_total: float | None = None,
) -> SipRecommendation:
    """
    surplus_before_investing_low: lower-band monthly surplus BEFORE any investing (from the forecast).
    emergency_gap: rupees still missing from the emergency fund (0 if complete).
    The emergency fund is topped up first, then a safety factor keeps a buffer. The top-up is
    never more than the surplus itself (a plan must not ask for money the month does not leave;
    found by the Phase 6B evaluation, rule R4).
    """
    if months_to_fill_emergency <= 0 or not 0 < safety_factor <= 1:
        raise ValueError("months_to_fill_emergency must be > 0 and safety_factor in (0, 1]")
    needed_topup = emergency_gap / months_to_fill_emergency if emergency_gap > 0 else 0.0
    topup = min(needed_topup, max(0.0, surplus_before_investing_low))
    capacity = max(0.0, surplus_before_investing_low * safety_factor - topup)

    if target_total is not None and target_total < capacity:
        recommended, limited_by = max(0.0, target_total), "target"
    else:
        recommended, limited_by = capacity, ("capacity" if capacity < (target_total or float("inf")) else "none")
    return SipRecommendation(
        capacity_total=round(capacity, 2), recommended_total=round(recommended, 2),
        change_vs_existing=round(recommended - existing_sip, 2), emergency_topup=round(topup, 2),
        limited_by=limited_by, emergency_topup_capped=needed_topup - topup > 0.005,
    )


def future_value_sip(monthly: float, annual_return: float, years: float, annual_step_up: float = 0.0) -> float:
    """
    Corpus after investing `monthly` at the end of each month for `years`, growing at
    `annual_return` (0.10 = 10%), with the SIP raised by `annual_step_up` every 12 months.
    """
    if monthly < 0 or years < 0 or annual_return <= -1:
        raise ValueError("monthly and years must be >= 0 and annual_return > -100%")
    monthly_rate = (1 + annual_return) ** (1 / 12) - 1
    balance, contribution = 0.0, monthly
    for month in range(1, int(round(years * 12)) + 1):
        balance = balance * (1 + monthly_rate) + contribution
        if month % 12 == 0:
            contribution *= 1 + annual_step_up
    return balance


def required_sip(goal: float, annual_return: float, years: float) -> float:
    """Flat monthly SIP needed to reach `goal` (no step-up), under the assumed return."""
    unit = future_value_sip(1.0, annual_return, years)
    if unit <= 0:
        raise ValueError("years must be at least one month")
    return goal / unit


def months_to_reach(goal: float, monthly: float, annual_return: float, max_months: int = 1200) -> int | None:
    """
    Months of investing `monthly` needed to reach `goal` (flat SIP). Returns None if it would
    take longer than `max_months` (100 years) or the SIP is zero. Used for sentences like
    "raising your SIP by Rs 2,000 reaches the goal N months earlier".
    """
    if goal <= 0:
        return 0
    if monthly <= 0:
        return None
    monthly_rate = (1 + annual_return) ** (1 / 12) - 1
    balance = 0.0
    for month in range(1, max_months + 1):
        balance = balance * (1 + monthly_rate) + monthly
        if balance >= goal:
            return month
    return None
