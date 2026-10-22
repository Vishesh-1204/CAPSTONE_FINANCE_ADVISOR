# tests/test_budget_calculator.py
#
# Unit tests for the deterministic budget maths (no DB, no LLM).
# The first eight are the verified appendix tests from docs/phase4-spec.md,
# adapted to Decimal; the rest pin down the v3-specific rules.

import random
from decimal import Decimal

import pytest

from app.core.categories import CATEGORY_NAMES, INVESTMENTS_CATEGORY, TRANSFERS_CATEGORY
from app.services.budget_calculator import (
    BASELINE_WEIGHTS, ESSENTIAL_CATEGORIES, METHOD_BASELINE, METHOD_HISTORY, SAVINGS_CATEGORY,
    build_baseline_plan, build_history_plan, compute_spendable, is_essential, savings_target_amount,
)


def test_baseline_weights_sum_to_one():
    assert sum(BASELINE_WEIGHTS.values()) == Decimal("1")


def test_spendable_income_after_savings_target():
    assert compute_spendable(100_000, 20) == Decimal("80000.00")


def test_baseline_never_exceeds_spendable():
    plan = build_baseline_plan(income=Decimal("57321.55"), savings_target_pct=18)
    assert sum(plan.limits.values()) <= plan.spendable
    assert plan.method == METHOD_BASELINE


def test_history_plan_fits_without_scaling():
    avg = {"Housing & Rent": 30_000, "Groceries": 8_000, "Food & Dining": 10_000, "Entertainment": 6_000, "Shopping": 12_000}
    plan = build_history_plan(100_000, 20, avg, discretionary_trim=Decimal("0.10"))
    assert plan.feasible and plan.method == METHOD_HISTORY
    assert plan.limits["Housing & Rent"] == Decimal("30000.00")   # essentials untouched
    assert plan.limits["Food & Dining"] == Decimal("9000.00")     # discretionary trimmed 10%
    assert plan.discretionary_scale == 1
    assert sum(plan.limits.values()) <= plan.spendable


def test_history_plan_scales_discretionary_to_fit():
    avg = {"Housing & Rent": 25_000, "Groceries": 6_000, "Food & Dining": 10_000, "Shopping": 10_000}
    plan = build_history_plan(50_000, 20, avg, discretionary_trim=Decimal("0.10"))   # spendable = 40,000
    assert plan.feasible
    assert plan.limits["Food & Dining"] == Decimal("4500.00")
    assert plan.discretionary_scale == Decimal("0.5")
    assert sum(plan.limits.values()) <= plan.spendable


def test_infeasible_when_essentials_exceed_income():
    avg = {"Housing & Rent": 25_000, "Shopping": 5_000}
    plan = build_history_plan(30_000, 20, avg, discretionary_trim=Decimal("0.10"))   # spendable = 24,000
    assert not plan.feasible
    assert plan.shortfall == Decimal("1000.00")
    assert plan.limits["Shopping"] == 0
    assert plan.limits["Housing & Rent"] == Decimal("25000.00")        # essentials never cut


def test_empty_history_falls_back_to_baseline():
    plan = build_history_plan(60_000, 20, {}, discretionary_trim=Decimal("0.10"))
    assert plan.method == METHOD_BASELINE


def test_savings_category_is_never_budgeted():
    plan = build_history_plan(60_000, 20, {SAVINGS_CATEGORY: 9_000, "Groceries": 5_000}, Decimal("0.10"))
    assert SAVINGS_CATEGORY not in plan.limits


# --- v3-specific rules ---

def test_weights_and_essentials_use_real_expense_categories():
    """A typo in a category name would silently create a limit nothing can ever match."""
    assert set(BASELINE_WEIGHTS) <= set(CATEGORY_NAMES)
    assert ESSENTIAL_CATEGORIES <= set(CATEGORY_NAMES)
    assert SAVINGS_CATEGORY == INVESTMENTS_CATEGORY
    assert INVESTMENTS_CATEGORY not in BASELINE_WEIGHTS


def test_transfers_are_discretionary_with_a_small_weight():
    assert not is_essential(TRANSFERS_CATEGORY)
    assert BASELINE_WEIGHTS[TRANSFERS_CATEGORY] <= Decimal("0.05")
    plan = build_history_plan(50_000, 20, {"Groceries": 5_000, TRANSFERS_CATEGORY: 2_000}, Decimal("0.10"))
    assert plan.limits[TRANSFERS_CATEGORY] == Decimal("1800.00")     # trimmed like any discretionary


def test_float_inputs_are_accepted_without_float_drift():
    # 0.1 + 0.2 style inputs must not create stray paise
    plan = build_history_plan(60_000.10, 20, {"Shopping": 0.1 + 0.2}, 0.10)
    assert all(limit == limit.quantize(Decimal("0.01")) for limit in plan.limits.values())


def test_limits_have_exactly_two_decimals_and_round_down():
    plan = build_history_plan(90_000, 20, {"Groceries": Decimal("10000") / 3}, Decimal("0.10"))
    assert plan.limits["Groceries"] == Decimal("3333.33")


def test_savings_target_amount_plus_spendable_is_income():
    assert savings_target_amount(57_321.55, 18) + compute_spendable(57_321.55, 18) == Decimal("57321.55")


def test_trim_outside_zero_to_one_is_rejected():
    with pytest.raises(ValueError):
        build_history_plan(60_000, 20, {"Shopping": 1_000}, discretionary_trim=10)


def test_random_plans_never_exceed_spendable_and_are_never_negative():
    """200 seeded random histories: the core guarantee of the rules engine."""
    rng = random.Random(42)
    categories = [c for c in CATEGORY_NAMES if c != SAVINGS_CATEGORY]
    for _ in range(200):
        income = Decimal(rng.randint(5_000, 300_000)) + Decimal(rng.randint(0, 99)) / 100
        pct = Decimal(rng.randint(0, 60))
        avg = {c: Decimal(rng.randint(1, 4_000_000)) / 300 for c in rng.sample(categories, rng.randint(1, len(categories)))}
        trim = Decimal(rng.choice(["0", "0.05", "0.10", "0.20"]))
        plan = build_history_plan(income, pct, avg, trim)

        assert all(limit >= 0 for limit in plan.limits.values())
        discretionary = [v for c, v in plan.limits.items() if not is_essential(c)]
        if plan.feasible:
            assert sum(plan.limits.values()) <= plan.spendable
        else:
            assert plan.shortfall > 0 and all(v == 0 for v in discretionary)
