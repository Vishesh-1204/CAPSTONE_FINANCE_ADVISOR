# app/services/budget_calculator.py
#
# Purpose: the deterministic "rules engine" of the Budget Planning Agent.
# Pure maths, no database, no LLM: given income, a savings target and the
# user's average monthly spending per category, it returns a limit per
# category that ALWAYS fits the spendable income (or says honestly that it
# cannot). The LLM later only explains these numbers; it never computes them.
#
# Everything is Decimal (like the Numeric(14,2) columns), so limits are exact
# to the paisa and never drift the way floats do.

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from app.core.categories import INVESTMENTS_CATEGORY, TRANSFERS_CATEGORY
from app.core.money import to_money

# Savings are handled by the savings target, not by a spending limit
SAVINGS_CATEGORY = INVESTMENTS_CATEGORY

METHOD_HISTORY = "history"     # built from the user's own average spending
METHOD_BASELINE = "baseline"   # fixed starter split (no history, and research baseline B0)

# Must-pay categories. We never cut these below what the user actually spends.
ESSENTIAL_CATEGORIES: frozenset[str] = frozenset({
    "Housing & Rent", "Groceries", "Utilities", "Transportation",
    "Healthcare", "Insurance", "Education",
})

# Starter split of spendable income, used when there is no history.
# Weights sum to exactly 1 (checked in the unit tests); Transfers (money sent to
# people) is discretionary with a small weight.
BASELINE_WEIGHTS: dict[str, Decimal] = {
    "Housing & Rent": Decimal("0.28"), "Groceries": Decimal("0.12"), "Utilities": Decimal("0.07"),
    "Transportation": Decimal("0.08"), "Healthcare": Decimal("0.04"), "Insurance": Decimal("0.05"),
    "Education": Decimal("0.05"), "Food & Dining": Decimal("0.08"), "Entertainment": Decimal("0.05"),
    "Shopping": Decimal("0.06"), "Travel": Decimal("0.04"), "Personal Care": Decimal("0.02"),
    "Subscriptions": Decimal("0.02"), TRANSFERS_CATEGORY: Decimal("0.02"), "Other": Decimal("0.02"),
}

_PAISE = Decimal("0.01")
_ZERO = Decimal("0.00")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")

Number = Decimal | int | float | str


@dataclass(frozen=True)
class BudgetPlan:
    """The calculator's output: limits plus whether they fit the income."""
    limits: dict[str, Decimal]     # category -> monthly limit
    spendable: Decimal             # income minus the savings target
    feasible: bool                 # False if essentials alone exceed spendable
    shortfall: Decimal             # amount essentials exceed spendable (0 if feasible)
    method: str                    # METHOD_HISTORY or METHOD_BASELINE
    discretionary_scale: Decimal   # 1 = trimmed only, <1 = also scaled down to fit, 0 = infeasible


def _dec(value: Number) -> Decimal:
    """Any number -> Decimal. Going through str() avoids importing a float's binary error."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def is_essential(category: str) -> bool:
    """True for must-pay categories (rent, groceries, ...)."""
    return category in ESSENTIAL_CATEGORIES


def _floor_paise(value: Decimal) -> Decimal:
    """
    Rounds DOWN to 2 decimals. Rounding down guarantees the limits never add
    up to more than the spendable income because of rounding.
    """
    return value.quantize(_PAISE, rounding=ROUND_DOWN)


def _clamp_to_spendable(limits: dict[str, Decimal], spendable: Decimal, adjustable: list[str]) -> None:
    """
    Safety net: if the limits still exceed spendable (possible only by a
    paisa of division rounding), take the excess from the largest adjustable
    (discretionary) limits. Modifies `limits` in place.
    """
    excess = sum(limits.values(), _ZERO) - spendable
    for category in sorted(adjustable, key=lambda c: limits[c], reverse=True):
        if excess <= 0:
            break
        cut = min(excess, limits[category])
        limits[category] -= cut
        excess -= cut


def compute_spendable(income: Number, savings_target_pct: Number) -> Decimal:
    """Income left to spend after setting aside the savings target."""
    return _floor_paise(_dec(income) * (_HUNDRED - _dec(savings_target_pct)) / _HUNDRED)


def savings_target_amount(income: Number, savings_target_pct: Number) -> Decimal:
    """The rupee amount the savings target sets aside (income minus spendable)."""
    return to_money(_dec(income)) - compute_spendable(income, savings_target_pct)


def build_baseline_plan(income: Number, savings_target_pct: Number) -> BudgetPlan:
    """Splits spendable income using fixed weights. Used as a cold start and as a research baseline."""
    spendable = compute_spendable(income, savings_target_pct)
    limits = {cat: _floor_paise(spendable * w) for cat, w in BASELINE_WEIGHTS.items()}
    _clamp_to_spendable(limits, spendable, [c for c in limits if not is_essential(c)])
    return BudgetPlan(
        limits=limits, spendable=spendable, feasible=True, shortfall=_ZERO,
        method=METHOD_BASELINE, discretionary_scale=_ONE,
    )


def build_history_plan(
    income: Number,
    savings_target_pct: Number,
    avg_monthly_spend: dict[str, Number],
    discretionary_trim: Number,
) -> BudgetPlan:
    """
    Builds limits from the user's real average monthly spending:
      1. Essentials keep their historical average (we don't squeeze rent).
      2. Discretionary categories are trimmed by `discretionary_trim` (0.10 = 10%).
      3. If the total still exceeds spendable income, discretionary limits
         are scaled down proportionally until everything fits.
      4. If essentials alone don't fit, discretionary goes to zero and we
         report the shortfall honestly instead of hiding it.
    Falls back to the baseline plan when there is no usable history.
    """
    trim = _dec(discretionary_trim)
    if not _ZERO <= trim <= _ONE:
        raise ValueError(f"discretionary_trim must be between 0 and 1, got {discretionary_trim}")
    spendable = compute_spendable(income, savings_target_pct)

    averages = {c: _dec(a) for c, a in avg_monthly_spend.items() if c != SAVINGS_CATEGORY and _dec(a) > 0}
    if not averages:
        return build_baseline_plan(income, savings_target_pct)

    essentials = {c: a for c, a in averages.items() if is_essential(c)}
    discretionary = {c: a * (_ONE - trim) for c, a in averages.items() if not is_essential(c)}

    limits = {c: _floor_paise(a) for c, a in essentials.items()}

    available = spendable - sum(essentials.values(), _ZERO)   # room left for discretionary
    discretionary_total = sum(discretionary.values(), _ZERO)

    feasible, shortfall = True, _ZERO
    if available < 0:
        feasible, shortfall, scale = False, to_money(-available), _ZERO
    elif discretionary_total > available:
        scale = available / discretionary_total
    else:
        scale = _ONE

    for category, amount in discretionary.items():
        limits[category] = _floor_paise(amount * scale)

    if feasible:
        _clamp_to_spendable(limits, spendable, list(discretionary))

    return BudgetPlan(
        limits=limits, spendable=spendable, feasible=feasible, shortfall=shortfall,
        method=METHOD_HISTORY, discretionary_scale=scale,
    )
