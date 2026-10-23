# backend/evaluation/investing_methods.py
#
# Purpose: the methods compared in the Investment Advisor evaluation (Phase 6B).
# Written AFTER evaluation/investing_rules.py was committed (pre-registration).
#
# A method = an ALLOCATION rule + a SIP rule, so the two can be separated:
#   B0  fixed 60/40 equity/debt              + naive SIP
#   B1  "100 minus age" in equity, rest debt + naive SIP
#   B2  questionnaire only (tolerance category's base split, no capacity, no
#       horizon cap)                          + naive SIP
#   P   the shipped pipeline (tolerance vs capacity, horizon glide)
#                                             + P's SIP (emergency first, lower band x 0.8,
#                                               capped at what the goal needs)
#   Mixed variants (so nobody can say P wins only because of one half):
#   B2_alloc+P_sip   and   P_alloc+naive_sip
# naive SIP = the mean surplus x 1.0, no emergency reservation, no goal cap.
#
# P calls the SAME pure functions in the SAME order as services/investment_service.py
# (tests/test_investing_evaluation.py checks it against the real service). PConfig
# switches parts off for the ablations.

import math
from dataclasses import dataclass, replace
from typing import Literal

from app.core.config import settings
from app.services.investing.allocation import DEFAULT_POLICY, target_allocation
from app.services.investing.assumptions import InvestingAssumptions
from app.services.investing.portfolio_stats import portfolio_return_and_volatility
from app.services.investing.risk_scoring import (
    CapacityFacts, ToleranceAnswers, build_risk_profile, categorize, tolerance_score,
)
from app.services.investing.sip_planner import recommend_sip, required_sip
from evaluation.investing_personas import Persona
from evaluation.investing_rules import Recommendation

Z_75 = 0.6745        # one-sided 75th percentile of the standard normal (goal_sizing="p75")


@dataclass(frozen=True)
class PConfig:
    use_capacity: bool = True         # False: tolerance only (no capacity score)
    horizon_glide: bool = True        # False: no equity cap for short horizons
    emergency_first: bool = True      # False: no emergency top-up reserved
    safety_factor: float = 0.8
    surplus_band: Literal["low", "mean"] = "low"
    goal_sizing: Literal["expected", "p75"] = "expected"


DEFAULT_P = PConfig()

# P ablations (label -> config), all relative to the shipped defaults
ABLATIONS: dict[str, PConfig] = {
    "no_capacity": replace(DEFAULT_P, use_capacity=False),
    "no_horizon_glide": replace(DEFAULT_P, horizon_glide=False),
    "no_emergency_first": replace(DEFAULT_P, emergency_first=False),
    "safety=1.0": replace(DEFAULT_P, safety_factor=1.0),
    "safety=0.6": replace(DEFAULT_P, safety_factor=0.6),
    "mean_surplus": replace(DEFAULT_P, surplus_band="mean"),
    "goal_p75": replace(DEFAULT_P, goal_sizing="p75"),
}


@dataclass(frozen=True)
class MethodPlan:
    weights: dict[str, float]
    monthly_sip: float
    emergency_topup: float
    expected_return: float
    volatility: float

    def recommendation(self) -> Recommendation:
        return Recommendation(self.weights.get("equity", 0.0), self.monthly_sip, self.emergency_topup)


# --- Allocation rules ---

def allocation_60_40(persona: Persona) -> dict[str, float]:
    return {"equity": 0.60, "debt": 0.40, "gold": 0.0, "cash": 0.0}


def allocation_age(persona: Persona) -> dict[str, float]:
    equity = min(1.0, max(0.0, (100 - persona.age) / 100))
    return {"equity": round(equity, 4), "debt": round(1 - equity, 4), "gold": 0.0, "cash": 0.0}


def _answers(persona: Persona) -> ToleranceAnswers:
    return ToleranceAnswers(*persona.answers)


def allocation_tolerance_only(persona: Persona) -> dict[str, float]:
    return dict(DEFAULT_POLICY.base[categorize(tolerance_score(_answers(persona)))])


def allocation_p(persona: Persona, config: PConfig = DEFAULT_P) -> dict[str, float]:
    risk = build_risk_profile(
        _answers(persona),
        CapacityFacts(persona.emergency_months, persona.income_stability, persona.dependents, persona.horizon_years),
    )
    category = risk.category if config.use_capacity else risk.tolerance_category
    if config.horizon_glide:
        return target_allocation(category, persona.horizon_years)
    return dict(DEFAULT_POLICY.base[category])


# --- SIP rules ---

def sip_naive(persona: Persona, mean: float, volatility: float) -> tuple[float, float]:
    """(monthly SIP, emergency top-up): the whole mean surplus, nothing reserved."""
    return round(max(0.0, persona.mean_surplus), 2), 0.0


def _goal_rate(mean: float, volatility: float, years: float, sizing: str) -> float:
    """
    The return the goal's required SIP is sized at. 'expected' = the mean (about a
    50% chance by construction). 'p75' = a heuristic 25th-percentile annual return,
    mean - z75 x vol / sqrt(T/2) (T/2: on average a SIP rupee is invested half the
    horizon). The achieved goal probability is MEASURED, not assumed.
    """
    if sizing == "expected":
        return mean
    return mean - Z_75 * volatility / math.sqrt(max(years / 2, 0.5))


def sip_p(persona: Persona, mean: float, volatility: float, config: PConfig = DEFAULT_P) -> tuple[float, float]:
    """P's SIP rule, in the service's order: emergency gap, surplus band, goal cap, recommend_sip."""
    gap = 0.0
    if config.emergency_first:
        gap = max(0.0, persona.essential_monthly * settings.INVEST_EMERGENCY_TARGET_MONTHS - persona.liquid_savings)
    surplus = persona.low_surplus if config.surplus_band == "low" else persona.mean_surplus
    years = persona.horizon_years
    needed = None
    if round(years * 12) >= 1:
        rate = _goal_rate(mean, volatility, years, config.goal_sizing)
        needed = required_sip(persona.goal_amount, rate, years)
    sip = recommend_sip(
        surplus, persona.existing_sip, gap, months_to_fill_emergency=settings.INVEST_EMERGENCY_FILL_MONTHS,
        safety_factor=config.safety_factor, target_total=needed,
    )
    return sip.recommended_total, sip.emergency_topup


# --- Methods ---

def _plan(weights: dict[str, float], sip_rule, persona: Persona, assumptions: InvestingAssumptions) -> MethodPlan:
    mean, volatility = portfolio_return_and_volatility(weights, assumptions.assets)
    monthly, topup = sip_rule(persona, mean, volatility)
    return MethodPlan(weights, monthly, topup, mean, volatility)


def method_plan(name: str, persona: Persona, assumptions: InvestingAssumptions) -> MethodPlan:
    """One of METHODS for one persona."""
    if name == "B0":
        return _plan(allocation_60_40(persona), sip_naive, persona, assumptions)
    if name == "B1":
        return _plan(allocation_age(persona), sip_naive, persona, assumptions)
    if name == "B2":
        return _plan(allocation_tolerance_only(persona), sip_naive, persona, assumptions)
    if name == "P":
        return _plan(allocation_p(persona), sip_p, persona, assumptions)
    if name == "B2_alloc+P_sip":
        return _plan(allocation_tolerance_only(persona), sip_p, persona, assumptions)
    if name == "P_alloc+naive_sip":
        return _plan(allocation_p(persona), sip_naive, persona, assumptions)
    raise ValueError(f"unknown method: {name}")


def ablation_plan(label: str, persona: Persona, assumptions: InvestingAssumptions) -> MethodPlan:
    """P with one part changed (see ABLATIONS)."""
    config = ABLATIONS[label]
    return _plan(
        allocation_p(persona, config), lambda p, m, v: sip_p(p, m, v, config), persona, assumptions,
    )


METHODS: tuple[str, ...] = ("B0", "B1", "B2", "P", "B2_alloc+P_sip", "P_alloc+naive_sip")
