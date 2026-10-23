# backend/evaluation/investing_personas.py
#
# Purpose: SYNTHETIC personas for the Investment Advisor evaluation (Phase 6B).
# Every number is made up. Persona i draws from random.Random(seed * 1000 + i), so
# the same seed always gives identical personas and adding personas never changes
# the earlier ones (same convention as scripts/make_synthetic_panel.py).
#
# Each attribute is drawn INDEPENDENTLY (a stated limitation): unrealistic
# combinations occur, for example age 60 with a 25-year horizon, or a high income
# with no savings. The aim is coverage of the rule conditions, not realism.
#
# Surplus model: monthly spending = essentials + discretionary, with a standard
# deviation of cv x spending. The mean surplus is income - spending (before
# investing). The LOW surplus mirrors the forecast's lower band: the forecast uses
# the upper end of an 80% two-sided spending interval (the 90th percentile), so
# low = mean - 1.2816 x sd.
#
# Pure Python plus the pure sip_planner (for goal sizes). No database.

import math
import random
from dataclasses import dataclass
from decimal import Decimal

from app.services.investing.sip_planner import future_value_sip
from evaluation.investing_rules import PersonaFacts

DEFAULT_SEED = 42
DEFAULT_COUNT = 500
Z_90 = 1.2816                       # one-sided 90th percentile of the standard normal
GOAL_RETURN = 0.08                  # the return a persona has in mind when setting a goal (not P's assumption)
SHORT_HORIZON_SHARE = 0.05          # share of personas with a 6-month horizon (so rule R1 is exercised)

# Segments reported separately (raw facts only, like the rules)
SEGMENTS = ("all", "weak_emergency_fund", "short_horizon", "cautious", "strong")


@dataclass(frozen=True)
class Persona:
    persona_id: int
    seed: int                       # also the Monte Carlo seed for this persona (common random numbers)
    age: int
    monthly_income: float
    essential_monthly: float
    discretionary_monthly: float
    spending_sd: float
    mean_surplus: float             # monthly, before investing
    low_surplus: float              # monthly lower band, before investing
    liquid_savings: float
    emergency_months: float         # liquid savings / essentials, rounded like the service (2 decimals)
    income_stability: int
    dependents: int
    horizon_years: float
    answers: tuple[int, int, int]
    existing_sip: float
    goal_amount: float

    def facts(self) -> PersonaFacts:
        return PersonaFacts(
            age=self.age, horizon_years=self.horizon_years, emergency_months=self.emergency_months,
            income_stability=self.income_stability, dependents=self.dependents, answers=self.answers,
            essential_monthly=self.essential_monthly, liquid_savings=self.liquid_savings,
            low_surplus=self.low_surplus,
        )

    @property
    def segments(self) -> list[str]:
        groups = ["all"]
        if self.emergency_months < 3:
            groups.append("weak_emergency_fund")
        if self.horizon_years < 3:
            groups.append("short_horizon")
        if max(self.answers) <= 2:
            groups.append("cautious")
        if (self.horizon_years >= 10 and self.emergency_months >= 6 and self.income_stability >= 4
                and self.dependents <= 1 and min(self.answers) >= 4):
            groups.append("strong")
        return groups


def _round(value: float, step: int) -> float:
    return float(round(value / step) * step)


def emergency_months(liquid_savings: float, essential_monthly: float) -> float:
    """The service's formula (investment_service._emergency_months): Decimal division, 2 decimals."""
    if essential_monthly <= 0:
        return 0.0
    return round(float(Decimal(str(liquid_savings)) / Decimal(str(essential_monthly))), 2)


def make_persona(persona_id: int, seed: int = DEFAULT_SEED) -> Persona:
    persona_seed = seed * 1000 + persona_id
    rng = random.Random(persona_seed)
    income = _round(math.exp(rng.uniform(math.log(20_000), math.log(300_000))), 500)
    essentials = _round(income * rng.uniform(0.30, 0.60), 100)
    discretionary = _round(income * rng.uniform(0.05, 0.35), 100)
    spending = essentials + discretionary
    sd = round(spending * rng.uniform(0.05, 0.30), 2)
    mean_surplus = round(income - spending, 2)
    low_surplus = round(mean_surplus - Z_90 * sd, 2)
    savings = _round(rng.uniform(0, 12) * essentials, 100)
    horizon = 0.5 if rng.random() < SHORT_HORIZON_SHARE else float(rng.randint(1, 25))
    answers = (rng.randint(1, 5), rng.randint(1, 5), rng.randint(1, 5))
    existing_sip = _round(max(0.0, mean_surplus) * rng.uniform(0, 0.3), 100)
    goal_share = rng.uniform(0.2, 0.8)
    goal = max(10_000.0, _round(future_value_sip(max(1.0, mean_surplus * goal_share), GOAL_RETURN, horizon), 1000))
    return Persona(
        persona_id=persona_id, seed=persona_seed, age=rng.randint(22, 60), monthly_income=income,
        essential_monthly=essentials, discretionary_monthly=discretionary, spending_sd=sd,
        mean_surplus=mean_surplus, low_surplus=low_surplus, liquid_savings=savings,
        emergency_months=emergency_months(savings, essentials), income_stability=rng.randint(1, 5),
        dependents=rng.randint(0, 4), horizon_years=horizon, answers=answers, existing_sip=existing_sip,
        goal_amount=goal,
    )


def generate_personas(count: int = DEFAULT_COUNT, seed: int = DEFAULT_SEED) -> list[Persona]:
    return [make_persona(i, seed) for i in range(count)]
