# Phase 4 Spec: Budget Planning Agent (v3-aware)

Repo path (commit it here): `docs/phase4-spec.md`
Builds on the verified Phase 3B v3 backend (Alembic, `txn_type`, payee rules, summary endpoint).
This REPLACES my earlier Phase 4 design, which was written before v3 existed. The pure-maths
reference code in the appendix was re-verified (17 unit tests pass).

## 0. Ground rules for Claude Code (read first)

1. Read CLAUDE.md and this file. INSPECT the real code first (models, `core/categories.py`, `txn_type` values, session/deps patterns, test fixtures) and report any difference from this spec before editing.
2. Plan mode first. One step at a time (section 9). After each step: run `docker compose exec backend python -m pytest tests -q`, all green, one commit. Stop at every GATE and report.
3. Branch: `feature/vishesh-budget` (from the branch that contains v3; check `git branch -a` and tell me which base you used).
4. NO new dependencies. Everything here uses the existing stack.
5. Python does ALL the maths. The LLM only explains finished numbers, with ONE call per plan. Budgets must still work with no LLM key (template explanations).
6. Privacy: the LLM receives only category names and aggregate numbers. Never transaction descriptions, payees, UPI IDs or account details.
7. Layering as in CLAUDE.md. `user_id` from the JWT only; every query filters by `user_id`; someone else's id returns 404.
8. Teach as you go in beginner-friendly language. File path + purpose comment at the top of each new file; type hints, logging, no hardcoded values (tuning knobs in config).
9. Do not touch `.env`. Synthetic data only. Update CLAUDE.md "Current status" at the end.

## 1. What changed versus my earlier design

- Alembic already exists: add migration `0003` instead of setting Alembic up.
- Spending history = rows with `txn_type='expense'` only (investments, redemptions, income and transfers never count as spending). Verify the exact enum values in the code.
- `amount` is Numeric(14,2): use Decimal in the DB layer and convert to float at the service boundary, or make the calculator Decimal-safe. New money columns are Numeric(14,2), not Float.
- `Transfers` is now an expense category (person-to-person payments). Treat it as discretionary, with a small baseline weight.
- Budgets must report data quality (uncategorized and needs_review counts) so the user knows when the plan is built on weak data.
- The calculator must take its category lists from `app/core/categories.py` where possible (a test asserts every baseline-weight key is a real expense category).

## 2. Overview and why

The user sets income and a savings target. The planner reads the last N months of categorized EXPENSES, produces a limit per category with a reason, and the status endpoint compares actual spending to those limits.
- Deterministic maths is unit-testable and always sums to the spendable income; an LLM cannot guarantee that.
- One LLM call per plan fits the free-tier rate limit.
- Manual overrides are preserved on regeneration, and the agent's original limit is kept for the override-rate metric.
- A faithfulness guard rejects LLM text that mentions a rupee amount that is not in the plan. That is both a safety feature and a research metric.

## 3. Database (Alembic revision 0003)

- `financial_profiles`: id, user_id UNIQUE FK, monthly_income Numeric(14,2), savings_target_pct Numeric(5,2) default 20, updated_at.
- `budgets`: id, user_id FK indexed, month String(7) 'YYYY-MM', category, limit_amount Numeric(14,2), `agent_limit_amount` Numeric(14,2) (the planner's original number, never changed after creation/regeneration), source 'agent'|'user', reasoning String(1000), created_at; UNIQUE(user_id, month, category).
- `budget_runs` (research log, one row per generation): id, user_id, month, method ('history'|'baseline'), spendable Numeric, feasible bool, shortfall Numeric, months_used int, llm_used bool, explanation_faithful bool NULL, created_at.
- Add all models to `app/db/base.py`. Verify the migration applies on an empty DB and on the current DB.
- Config (env-overridable): `BUDGET_LOOKBACK_MONTHS=3`, `BUDGET_DISCRETIONARY_TRIM=0.10`, `BUDGET_WARNING_THRESHOLD=0.80`.

## 4. Behaviour

- Lookback window is the N full months BEFORE the target month. Average per category = total / number of distinct months that have data.
- History plan: essentials keep their average; discretionary is trimmed by the trim fraction, then scaled down proportionally if the total still exceeds spendable income; if essentials alone exceed spendable, discretionary goes to zero and `feasible=false` with the shortfall reported. No history -> baseline plan.
- Limits are rounded DOWN to paise so they never sum above spendable.
- Regenerate: keep `source='user'` rows untouched, refresh agent rows (update `limit_amount` and `agent_limit_amount`), delete stale agent rows.
- Response includes `data_quality`: months_used, uncategorized_count, needs_review_count, transfers_total.
- Planner agent: build `facts` (category, limit, avg_monthly_spend, essential), one LLM call, parse JSON defensively (strip fences), then run `explanation_guard.is_faithful` on the summary and each reason against the allowed numbers (limits, averages, spendable, shortfall, savings amount). Any failing text is replaced by the deterministic template. Record `llm_used` and `explanation_faithful` in `budget_runs`.

## 5. API (all JWT-protected; Pydantic schemas; `month` validated as `^\d{4}-(0[1-9]|1[0-2])$`)

- `PUT /api/profile/financial`, `GET /api/profile/financial`
- `GET /api/profile/financial/suggestion`: average monthly income from `txn_type='income'` credits over the lookback (returns null and a message when there is none).
- `POST /api/budgets/generate?month=`: returns method, spendable_income, feasible, shortfall, summary, data_quality, budgets[].
- `GET /api/budgets/?month=`
- `PUT /api/budgets/{id}` (override, sets source='user'), `POST /api/budgets/{id}/reset` (back to `agent_limit_amount`, source='agent')
- `GET /api/budgets/status?month=`: per category limit, spent (expenses only), remaining, percent_used, status ok|warning|exceeded|unbudgeted; totals; plus `invested_this_month` (investment_out total) and `savings_target_amount`.

## 6. Tests

- Pure: calculator, month utils, explanation guard (appendix files; keep them and extend).
- Service/API (SQLite + fake LLM, same fixtures as v3): investments and credits excluded from history and status; user isolation (404 on another user's budget); overrides survive regeneration; reset works; baseline when no history; infeasible case; LLM down -> template text and `llm_used=false`; LLM invents an amount -> template used and `explanation_faithful=false`; month validation 422; limits never sum above spendable.
- Migration test: upgrade head on an empty DB.

## 7. Research plan (this is your IEEE contribution for Phase 4)

- Methods compared: B0 fixed-weights baseline (50/30/20-style, `build_baseline_plan`); B1 history-trim (rules engine); B2 history-trim + LLM explanations (same numbers as B1, so it isolates the effect of the explanation).
- Synthetic data: `scripts/make_synthetic_history.py` creates 6 months of transactions for 3 personas (frugal, overspender, irregular spender) with a fixed random seed. Build the plan from months 1-3 and evaluate it on months 4-6 (time-based split, never random).
- Metrics: per-category MAE and MAPE of limit vs next-month actual; adherence rate (share of categories at or under limit); feasibility rate; override rate and mean relative override size (using `agent_limit_amount`); faithfulness rate (guard pass rate); LLM calls per plan (1); optional small user survey (1-5 helpfulness of explanations) among teammates and classmates.
- Ablations: trim 0/5/10/20%; lookback 1/3/6 months; investments counted as spending vs excluded (budget distortion); guard on vs off (how often would an invented number have reached the user).
- Output: `evaluation/budget_metrics.csv` plus a short markdown table for the paper.

## 8. Frontend (Test Console, separate session)

New `/budgets` page: profile form with the income suggestion button; "Generate budget" for a chosen month; table of limits with reasoning and a source badge; inline limit edit and reset; status view with ok/warning/exceeded bars; a Recharts bar chart of spent vs limit; collapsible raw-response panel. Approved stack only.

## 9. Steps (stop at each GATE)

- P0: inspect code; report differences; create branch. GATE.
- P1: migration 0003 + models + schemas + config. GATE.
- P2: add the three pure modules and their tests from the appendix (adapt to the real categories module). GATE.
- P3: planner agent with guard integration and template fallback. GATE.
- P4: budget service, routes, status endpoint, service/API tests. GATE.
- P5: income suggestion endpoint. GATE.
- P6: synthetic history script, evaluation script, `budget_metrics.csv`. GATE.
- P7: docs (purpose, architecture, flow diagram, input, output, API, tables, testing) under docs/, update CLAUDE.md, full test run, PR into DEV. GATE.
- P8 (separate session): frontend `/budgets` page.

## Appendix: verified reference code (adapt, do not copy blindly)

Every file below passed its tests (17 passing). Adapt imports to the real project.

### `backend/app/core/month_utils.py`
```python
# app/core/month_utils.py

from datetime import datetime, timezone


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """
    Converts 'YYYY-MM' into a (start, end) pair of UTC datetimes.
    'start' is inclusive and 'end' is exclusive, so filtering with
    start <= date < end never double-counts midnight on the 1st.
    """
    year, mon = (int(part) for part in month.split("-"))
    start = datetime(year, mon, 1, tzinfo=timezone.utc)
    # December rolls over into January of the next year
    end = datetime(year + (mon == 12), (mon % 12) + 1, 1, tzinfo=timezone.utc)
    return start, end


def shift_month(start: datetime, delta_months: int) -> datetime:
    """Returns the first day of the month that is `delta_months` away from `start`."""
    # Counting months since year 0 turns the calendar into a plain integer
    total_months = start.year * 12 + (start.month - 1) + delta_months
    return datetime(total_months // 12, total_months % 12 + 1, 1, tzinfo=timezone.utc)
```

### `backend/app/services/budget_calculator.py`
```python
# app/services/budget_calculator.py

import math
from dataclasses import dataclass

# Savings are handled by the savings target, not by a spending limit
SAVINGS_CATEGORY = "Investments & Savings"

# Must-pay categories. We never cut these below what the user actually spends.
ESSENTIAL_CATEGORIES = frozenset({
    "Housing & Rent", "Groceries", "Utilities", "Transportation",
    "Healthcare", "Insurance", "Education",
})

# Starter split of spendable income, used when there is no history.
# Weights sum to 1.0 (checked in the unit tests).
BASELINE_WEIGHTS: dict[str, float] = {
    "Housing & Rent": 0.28, "Groceries": 0.12, "Utilities": 0.07,
    "Transportation": 0.08, "Healthcare": 0.04, "Insurance": 0.05,
    "Education": 0.05, "Food & Dining": 0.08, "Entertainment": 0.05,
    "Shopping": 0.06, "Travel": 0.04, "Personal Care": 0.02,
    "Subscriptions": 0.02, "Transfers": 0.02, "Other": 0.02,
}


@dataclass(frozen=True)
class BudgetPlan:
    """The calculator's output: limits plus whether they fit the income."""
    limits: dict[str, float]   # category -> monthly limit
    spendable: float           # income minus the savings target
    feasible: bool             # False if essentials alone exceed spendable
    shortfall: float           # amount essentials exceed spendable (0 if feasible)
    method: str                # 'history' or 'baseline'


def _floor_cents(value: float) -> float:
    """
    Rounds DOWN to 2 decimals. Rounding down guarantees the limits never
    add up to more than the spendable income because of rounding.
    The tiny +1e-9 stops float noise (e.g. 4499.999999) from losing a paisa.
    """
    return math.floor(value * 100 + 1e-9) / 100


def compute_spendable(income: float, savings_target_pct: float) -> float:
    """Income left to spend after setting aside the savings target."""
    return _floor_cents(income * (1 - savings_target_pct / 100))


def build_baseline_plan(income: float, savings_target_pct: float) -> BudgetPlan:
    """Splits spendable income using fixed weights. Used as a cold start and as a research baseline."""
    spendable = compute_spendable(income, savings_target_pct)
    limits = {cat: _floor_cents(spendable * w) for cat, w in BASELINE_WEIGHTS.items()}
    return BudgetPlan(limits=limits, spendable=spendable, feasible=True, shortfall=0.0, method="baseline")


def build_history_plan(
    income: float,
    savings_target_pct: float,
    avg_monthly_spend: dict[str, float],
    discretionary_trim: float,
) -> BudgetPlan:
    """
    Builds limits from the user's real average monthly spending:
      1. Essentials keep their historical average (we don't squeeze rent).
      2. Discretionary categories are trimmed by `discretionary_trim`.
      3. If the total still exceeds spendable income, discretionary limits
         are scaled down proportionally until everything fits.
      4. If essentials alone don't fit, discretionary goes to zero and we
         report the shortfall honestly instead of hiding it.
    Falls back to the baseline plan when there is no usable history.
    """
    spendable = compute_spendable(income, savings_target_pct)

    averages = {c: a for c, a in avg_monthly_spend.items() if c != SAVINGS_CATEGORY and a > 0}
    if not averages:
        return build_baseline_plan(income, savings_target_pct)

    essentials = {c: a for c, a in averages.items() if c in ESSENTIAL_CATEGORIES}
    discretionary = {c: a * (1 - discretionary_trim) for c, a in averages.items() if c not in ESSENTIAL_CATEGORIES}

    limits = {c: _floor_cents(a) for c, a in essentials.items()}

    available = spendable - sum(essentials.values())  # room left for discretionary
    discretionary_total = sum(discretionary.values())

    feasible, shortfall = True, 0.0
    if available < 0:
        feasible, shortfall, scale = False, round(-available, 2), 0.0
    elif discretionary_total > available:
        scale = available / discretionary_total
    else:
        scale = 1.0

    for category, amount in discretionary.items():
        limits[category] = _floor_cents(amount * scale)

    return BudgetPlan(limits=limits, spendable=spendable, feasible=feasible, shortfall=shortfall, method="history")
```

### `backend/app/services/explanation_guard.py`
```python
# app/services/explanation_guard.py
#
# Purpose: the LLM is only allowed to EXPLAIN numbers the rules engine already
# produced. This module checks that an LLM-written explanation does not contain
# a rupee amount that is not in the plan. If it does, the caller discards the
# LLM text and uses the deterministic template instead. It is also a measurable
# "faithfulness" metric for the research paper (how often does the LLM invent a number?).

import re

# Matches rupee amounts such as ₹4,170 / ₹ 4170.50 / Rs. 9,000 / INR 1,200
_AMOUNT_PATTERN = re.compile(r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE)


def extract_rupee_amounts(text: str) -> list[float]:
    """Returns every rupee amount mentioned in `text` as a float."""
    amounts: list[float] = []
    for raw in _AMOUNT_PATTERN.findall(text):
        # "₹4,170." at the end of a sentence leaves a trailing dot after the capture, so strip commas only
        cleaned = raw.replace(",", "").rstrip(".")
        if cleaned:
            amounts.append(float(cleaned))
    return amounts


def find_unsupported_amounts(text: str, allowed: list[float], tolerance: float = 1.0) -> list[float]:
    """
    Returns the amounts in `text` that are NOT within `tolerance` rupees of any
    allowed number. The tolerance lets the LLM round (4170.4 -> 4,170).
    """
    unsupported: list[float] = []
    for amount in extract_rupee_amounts(text):
        if not any(abs(amount - value) <= tolerance for value in allowed):
            unsupported.append(amount)
    return unsupported


def is_faithful(text: str, allowed: list[float], tolerance: float = 1.0) -> bool:
    """True when the explanation mentions no amount outside the plan's numbers."""
    return not find_unsupported_amounts(text, allowed, tolerance)
```

### `backend/tests/test_month_utils.py`
```python
# tests/test_month_utils.py

from datetime import datetime, timezone

from app.core.month_utils import month_bounds, shift_month


def test_month_bounds_regular_month():
    start, end = month_bounds("2026-10")
    assert start == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_month_bounds_december_rolls_into_next_year():
    _, end = month_bounds("2026-12")
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_shift_month_crosses_year_boundary():
    start, _ = month_bounds("2026-02")
    assert shift_month(start, -3) == datetime(2025, 11, 1, tzinfo=timezone.utc)
```

### `backend/tests/test_budget_calculator.py`
```python
# tests/test_budget_calculator.py

import pytest

from app.services.budget_calculator import (
    BASELINE_WEIGHTS, SAVINGS_CATEGORY, build_baseline_plan, build_history_plan, compute_spendable,
)


def test_baseline_weights_sum_to_one():
    assert sum(BASELINE_WEIGHTS.values()) == pytest.approx(1.0)


def test_spendable_income_after_savings_target():
    assert compute_spendable(100_000, 20) == 80_000


def test_baseline_never_exceeds_spendable():
    plan = build_baseline_plan(income=57_321.55, savings_target_pct=18)
    assert sum(plan.limits.values()) <= plan.spendable
    assert plan.method == "baseline"


def test_history_plan_fits_without_scaling():
    avg = {"Housing & Rent": 30_000, "Groceries": 8_000, "Food & Dining": 10_000, "Entertainment": 6_000, "Shopping": 12_000}
    plan = build_history_plan(100_000, 20, avg, discretionary_trim=0.10)
    assert plan.feasible and plan.method == "history"
    assert plan.limits["Housing & Rent"] == pytest.approx(30_000)   # essentials untouched
    assert plan.limits["Food & Dining"] == pytest.approx(9_000)     # discretionary trimmed 10%
    assert sum(plan.limits.values()) <= plan.spendable


def test_history_plan_scales_discretionary_to_fit():
    avg = {"Housing & Rent": 25_000, "Groceries": 6_000, "Food & Dining": 10_000, "Shopping": 10_000}
    plan = build_history_plan(50_000, 20, avg, discretionary_trim=0.10)   # spendable = 40,000
    assert plan.feasible
    assert plan.limits["Food & Dining"] == pytest.approx(4_500)
    assert sum(plan.limits.values()) <= plan.spendable


def test_infeasible_when_essentials_exceed_income():
    avg = {"Housing & Rent": 25_000, "Shopping": 5_000}
    plan = build_history_plan(30_000, 20, avg, discretionary_trim=0.10)   # spendable = 24,000
    assert not plan.feasible
    assert plan.shortfall == pytest.approx(1_000)
    assert plan.limits["Shopping"] == 0
    assert plan.limits["Housing & Rent"] == pytest.approx(25_000)        # essentials never cut


def test_empty_history_falls_back_to_baseline():
    plan = build_history_plan(60_000, 20, {}, discretionary_trim=0.10)
    assert plan.method == "baseline"


def test_savings_category_is_never_budgeted():
    plan = build_history_plan(60_000, 20, {SAVINGS_CATEGORY: 9_000, "Groceries": 5_000}, 0.10)
    assert SAVINGS_CATEGORY not in plan.limits
```

### `backend/tests/test_explanation_guard.py`
```python
# tests/test_explanation_guard.py

from app.services.explanation_guard import extract_rupee_amounts, find_unsupported_amounts, is_faithful


def test_extracts_amounts_in_different_formats():
    text = "Spend ₹4,170 on food, Rs. 9000 on rent and INR 1,200.50 on fuel."
    assert extract_rupee_amounts(text) == [4170.0, 9000.0, 1200.5]


def test_numbers_without_a_currency_marker_are_ignored():
    assert extract_rupee_amounts("You have 3 months of data and a 10% trim.") == []


def test_faithful_when_all_amounts_come_from_the_plan():
    assert is_faithful("Food is capped at ₹4,170.", allowed=[4170.0, 9000.0])


def test_rounding_within_tolerance_is_allowed():
    assert is_faithful("Food is capped at ₹4,170.", allowed=[4170.4])


def test_invented_amount_is_caught():
    bad = find_unsupported_amounts("Cut dining to ₹2,500 and save ₹4,170.", allowed=[4170.0])
    assert bad == [2500.0]


def test_trailing_full_stop_is_not_part_of_the_number():
    assert extract_rupee_amounts("Rent is ₹9,000.") == [9000.0]
```
