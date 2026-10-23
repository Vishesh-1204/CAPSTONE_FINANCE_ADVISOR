# tests/test_risk_scoring.py

import pytest

from app.services.investing.risk_scoring import (
    AGGRESSIVE, CONSERVATIVE, MODERATE, CapacityFacts, ToleranceAnswers, build_risk_profile,
    capacity_score, categorize, tolerance_score,
)

BRAVE = ToleranceAnswers(5, 5, 5)
CAUTIOUS = ToleranceAnswers(1, 1, 1)
STRONG = CapacityFacts(emergency_fund_months=8, income_stability=5, dependents=0, horizon_years=12)
WEAK = CapacityFacts(emergency_fund_months=1, income_stability=2, dependents=3, horizon_years=2)


def test_tolerance_scale_endpoints():
    assert tolerance_score(CAUTIOUS) == 0
    assert tolerance_score(BRAVE) == 100


def test_answers_outside_one_to_five_are_rejected():
    with pytest.raises(ValueError):
        tolerance_score(ToleranceAnswers(0, 3, 3))


def test_strong_finances_score_high_capacity():
    assert capacity_score(STRONG) == 100


def test_no_emergency_fund_caps_capacity_even_with_everything_else_strong():
    facts = CapacityFacts(emergency_fund_months=1, income_stability=5, dependents=0, horizon_years=15)
    assert capacity_score(facts) <= 40


def test_more_dependents_never_raise_capacity():
    scores = [capacity_score(CapacityFacts(6, 4, d, 8)) for d in range(5)]
    assert scores == sorted(scores, reverse=True)


def test_category_thresholds():
    assert categorize(10) == CONSERVATIVE and categorize(50) == MODERATE and categorize(90) == AGGRESSIVE


def test_brave_person_with_weak_finances_is_limited_by_capacity():
    profile = build_risk_profile(BRAVE, WEAK)
    assert profile.tolerance_category == AGGRESSIVE
    assert profile.category == CONSERVATIVE and profile.limiting_factor == "capacity"


def test_cautious_person_with_strong_finances_is_limited_by_tolerance():
    profile = build_risk_profile(CAUTIOUS, STRONG)
    assert profile.category == CONSERVATIVE and profile.limiting_factor == "tolerance"


def test_brave_and_strong_is_aggressive():
    assert build_risk_profile(BRAVE, STRONG).category == AGGRESSIVE
