# app/services/investing/risk_scoring.py
#
# Purpose: turn a short questionnaire plus facts from the user's own statements into a risk
# category. Two separate ideas are scored and the SAFER one wins:
#   - risk TOLERANCE: how the person feels about losses (questionnaire)
#   - risk CAPACITY:  how much loss their finances can actually absorb (emergency fund,
#     income stability, dependents, time horizon)
# Someone who is brave but has no emergency fund should not be told to go aggressive.
# Pure Python: no database, no LLM. All thresholds are named constants (design choices,
# not market facts) so they can be tuned and ablated.

from dataclasses import dataclass

CONSERVATIVE, MODERATE, AGGRESSIVE = "conservative", "moderate", "aggressive"
_ORDER = (CONSERVATIVE, MODERATE, AGGRESSIVE)

# Score (0-100) below which a person is "conservative" / "moderate"; otherwise "aggressive"
CONSERVATIVE_BELOW = 35.0
MODERATE_BELOW = 65.0

# Fewer emergency-fund months than this caps capacity, whatever else is true
EMERGENCY_FLOOR_MONTHS = 3.0
CAPACITY_CAP_WITHOUT_EMERGENCY_FUND = 40.0
EMERGENCY_FULL_SCORE_MONTHS = 6.0
HORIZON_FULL_SCORE_YEARS = 10.0
PER_DEPENDENT_PENALTY = 20.0


@dataclass(frozen=True)
class ToleranceAnswers:
    """Each answer is 1 (most cautious) to 5 (most comfortable with risk)."""
    reaction_to_20pct_drop: int   # 1 = sell everything ... 5 = buy more
    investing_experience: int     # 1 = none ... 5 = many years
    return_vs_safety: int         # 1 = safety first ... 5 = maximum growth


@dataclass(frozen=True)
class CapacityFacts:
    """Facts about the person's finances. Emergency months is computed from their statements."""
    emergency_fund_months: float  # liquid savings / average monthly essential spending
    income_stability: int         # 1 = irregular ... 5 = very stable
    dependents: int               # people who rely on this income
    horizon_years: float          # years until the money is needed


@dataclass(frozen=True)
class RiskProfile:
    tolerance_score: float
    capacity_score: float
    tolerance_category: str
    capacity_category: str
    category: str                 # the final, safer of the two
    limiting_factor: str          # 'tolerance' or 'capacity' (or 'both' when equal)


def _check_scale(name: str, value: int) -> None:
    if not 1 <= value <= 5:
        raise ValueError(f"{name} must be between 1 and 5")


def tolerance_score(answers: ToleranceAnswers) -> float:
    """Average of the three answers, rescaled from 1-5 to 0-100."""
    values = (answers.reaction_to_20pct_drop, answers.investing_experience, answers.return_vs_safety)
    for name, value in zip(("reaction_to_20pct_drop", "investing_experience", "return_vs_safety"), values):
        _check_scale(name, value)
    return (sum(values) / len(values) - 1) / 4 * 100


def capacity_score(facts: CapacityFacts) -> float:
    """
    Average of four 0-100 parts. If the emergency fund is below the floor the whole score
    is capped, because investing before having a safety net is the classic mistake.
    """
    _check_scale("income_stability", facts.income_stability)
    if facts.emergency_fund_months < 0 or facts.dependents < 0 or facts.horizon_years < 0:
        raise ValueError("emergency months, dependents and horizon cannot be negative")

    emergency = min(facts.emergency_fund_months / EMERGENCY_FULL_SCORE_MONTHS, 1.0) * 100
    stability = (facts.income_stability - 1) / 4 * 100
    dependents = max(0.0, 100 - PER_DEPENDENT_PENALTY * facts.dependents)
    horizon = min(facts.horizon_years / HORIZON_FULL_SCORE_YEARS, 1.0) * 100

    score = (emergency + stability + dependents + horizon) / 4
    if facts.emergency_fund_months < EMERGENCY_FLOOR_MONTHS:
        score = min(score, CAPACITY_CAP_WITHOUT_EMERGENCY_FUND)
    return score


def categorize(score: float) -> str:
    if score < CONSERVATIVE_BELOW:
        return CONSERVATIVE
    if score < MODERATE_BELOW:
        return MODERATE
    return AGGRESSIVE


def build_risk_profile(answers: ToleranceAnswers, facts: CapacityFacts) -> RiskProfile:
    """Scores both sides and keeps the more cautious category."""
    tol, cap = tolerance_score(answers), capacity_score(facts)
    tol_cat, cap_cat = categorize(tol), categorize(cap)
    final = _ORDER[min(_ORDER.index(tol_cat), _ORDER.index(cap_cat))]
    if tol_cat == cap_cat:
        limiting = "both"
    else:
        limiting = "tolerance" if _ORDER.index(tol_cat) < _ORDER.index(cap_cat) else "capacity"
    return RiskProfile(round(tol, 1), round(cap, 1), tol_cat, cap_cat, final, limiting)
