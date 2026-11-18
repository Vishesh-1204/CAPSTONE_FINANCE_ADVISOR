# Phase 8 Spec: Goal Planning Agent

Status: design + VERIFIED reference code (27 unit tests pass). Build on `feature/vishesh-goals`, cut from `DEV` AFTER Phase 7 (`feature/vishesh-stocks`) is merged.
Educational planning tool, not financial advice. Every response and screen carries the disclaimer.

---

## 1. Overview

Users rarely save for "investing". They save for **goals**: a house down payment, a child's education, a car, a wedding, a vacation, retirement. The Goal Planning Agent:

1. Lets the user create several goals: name, type, target amount **in today's prices**, target date, priority (essential / important / aspirational), and money already saved for it.
2. **Inflates** each target to its price on the goal date (education and housing often rise faster than general prices, so inflation is per goal).
3. Works out the **monthly amount each goal needs**, counting the growth of money already saved. That fixes the Phase 6 limitation "projections start from Rs 0".
4. Gives each goal an **allocation suited to its own horizon** (a 1-year vacation fund is not invested like a 15-year education fund), reusing Phase 6's `target_allocation` and `portfolio_return_and_volatility`.
5. **Shares the one affordable monthly amount** (the Phase 6 SIP capacity) between goals by a priority waterfall: essential first, then important, then aspirational; nearest deadline first inside a priority.
6. For every goal that is not fully funded, shows **three honest levers**: add Rs X a month, move the date by N months, or aim for Rs Y by the original date. Also the PDF's sentence: "raise your SIP by Rs 2,000 to reach the target 10 months earlier".
7. Gives each goal a **chance of success** (seeded Monte Carlo, including money already saved).
8. **Tracks progress**: contributions are logged, and the goal says ahead / on track / behind against its plan.
9. One guarded LLM call explains the plan; template fallback.

### Not in Phase 8
- No automatic transfers or reminders. Those belong to Phase 9 (Automation, Celery).
- No chat interface. That is Phase 10 (Financial Coach).
- No tax-specific products or rules.

## 2. Why It Is Needed

- Completes the Goal Planning module of the project PDF and turns the Phase 6 "how much can I invest" into "what can I actually achieve, and by when".
- It is where the earlier agents meet: Phase 4 essentials and Phase 5 forecast feed the Phase 6 capacity, Phase 6 allocations give per-goal returns, Phase 7's bootstrap can give an optional history-based success probability.
- Research value: a clear, comparable allocation problem (waterfall vs proportional vs equal split) with measurable outcomes, plus two ablations that quantify common planning mistakes (ignoring inflation, ignoring money already saved).

## 3. Folder Location

```text
backend/app/
  services/goals/
    __init__.py
    goal_math.py           (reference code, pure)
    goal_allocator.py      (reference code, pure)
    goal_levers.py         (reference code, pure)
    goal_simulation.py     (reference code, pure)
    progress_tracker.py    (reference code, pure)
  services/goal_service.py         (orchestration: goals, capacity, plan, progress)
  agents/goal_explainer.py         (ONE LLM call, guards, template fallback)
  models/goal.py, schemas/goal.py, api/routes/goals.py
backend/migrations/versions/0007_goals.py
backend/evaluation/evaluate_goals.py (+ goals_* helpers, flat style)
backend/tests/test_goal_*.py
frontend/app/(console)/goals/page.tsx
docs/phase8-spec.md, docs/modules/goals.md, docs/modules/goals-evaluation.md
```

## 4. Module Documentation

### Purpose
Turn several life goals and one affordable monthly amount into a realistic, prioritised, explained plan, and track it over time.

### Architecture and flow

```text
 goals (user) ──► inflate target to goal date ──► target_future
                    months_left, current_saved ───────┐
 Phase 6 profile category ─► target_allocation(category, goal horizon)
 Phase 6 assumptions ──► portfolio_return_and_volatility ─► goal return r, volatility
                                                       ▼
                         goal_math.required_monthly ─► GoalNeed per goal
 Phase 6 SIP capacity (already net of emergency top-up) ─┐
                                                       ▼
                       goal_allocator.allocate_waterfall ─► assigned monthly per goal
                                                       ▼
            goal_levers (underfunded goals)    goal_simulation (chance of success)
                                                       ▼
            facts (aggregates, pre-formatted strings) ─► goal_explainer (1 LLM call, guards)
                                                       ▼
                         goal_plans row (stored) ─► API ─► /goals page
 contributions ──► progress_tracker ──► ahead / on track / behind
```

### Input
Goals, contributions, the Phase 6 risk profile and latest investment plan (SIP capacity, emergency top-up), INVEST_* assumptions, GOAL_* settings.

### Output (one stored goal plan)
For each goal: target today, inflated target, months left, allocation, assumed return, required monthly, assigned monthly, funded fraction, status, levers, success probability with p10/p50/p90, progress status. Plan-level: capacity used and left, number of goals by status, warnings, explanation, `explanation_source`, assumptions, disclaimer.

### Database tables (migration 0007)
- `goals`: id, user_id (FK, indexed), name String(100), goal_type String(20) with CHECK (house, education, vehicle, wedding, travel, retirement, gadget, other), priority String(15) with CHECK (essential, important, aspirational), target_amount_today Numeric(14,2) > 0, target_date Date, current_saved Numeric(14,2) >= 0, inflation_override Numeric(6,4) nullable, status String(12) with CHECK (active, achieved, archived), created_at, updated_at. Per-user limit from settings (default 10).
- `goal_contributions`: id, goal_id (FK, cascade), user_id (FK), amount Numeric(14,2) (positive = added, negative = withdrawn), contributed_on Date, note String(200). Index (goal_id, contributed_on).
- `goal_plans`: id, user_id (FK), created_at, investment_plan_id (FK nullable, the capacity source), inputs_json, result_json, assumptions_json (with seed), explanation, llm_used, explanation_faithful, claims_compliant, llm_calls, raw_prohibited_claims, engine_version. Index (user_id, created_at DESC).
- `current_saved` on the goal is the starting balance; the live balance = current_saved + sum of contributions. Never store the live balance twice.

### API design (all under `/api/goals`, JWT required, user_id from the token only)

| Method | Path | Purpose |
|---|---|---|
| POST | `` (root) | Create a goal (validated, limit, date must be at least 1 month away) |
| GET | `` | List goals with live balance and latest status |
| GET / PATCH / DELETE | `/{goal_id}` | Read, edit, archive (DELETE archives, never hard-deletes history) |
| POST | `/{goal_id}/contributions` | Log a contribution or withdrawal |
| GET | `/{goal_id}/progress` | Ahead / on track / behind against the latest plan |
| POST | `/plan` | Generate a plan for all active goals (stored) |
| GET | `/plan/latest`, `/plans`, `/plans/{plan_id}` | Latest, history, one full plan (404 if not yours) |
| POST | `/what-if` | Change one goal's monthly amount, date or target and see levers and probability. Not stored |

### Testing strategy
1. Unit tests on the five pure modules (27, written and passing).
2. Service tests with a seeded user, a stubbed Phase 6 capacity, and three or four goals: priority order respected, inflation applied, saved money reduces need, achieved goal skipped, capacity never exceeded, missing investment plan gives a clear 409.
3. Explainer tests with a stub LLM (valid accepted, invented number rejected, promise wording rejected, failure gives template, exactly one call with a single stub).
4. Route tests: auth, user isolation for goals, contributions and plans, validation, disclaimer.
5. Smoke test extended by one step (create two goals, add a contribution, generate a plan, read progress).
6. Evaluation (section 9).

## 5. Key design decisions

1. **Emergency fund is NOT a user goal.** Phase 6 already reserves an emergency top-up before computing the SIP capacity. Letting users also create an "emergency fund" goal would count it twice. The plan shows the Phase 6 top-up as a read-only "safety net" row instead.
2. **Capacity comes from Phase 6, not recomputed.** The plan uses the latest stored investment plan's `capacity_total` and records its id. If there is none, return 409 "generate an investment plan first". If it is older than the latest forecast, add the warning `capacity_stale`.
3. **Per-goal allocation.** Each goal's return and volatility come from `target_allocation(profile category, goal horizon in years)` and the INVEST_* assumptions. Short goals automatically get little or no equity (the Phase 6 horizon caps).
4. **Inflation per goal.** `GOAL_DEFAULT_INFLATION` (illustrative placeholder 0.06) with an optional per-type default (for example education 0.08, set in settings) and a per-goal override. **These numbers are placeholders to be cited before the paper**, exactly like the Phase 6 return assumptions.
5. **Waterfall with a fair tie-break.** Priority, then nearest deadline, then goal id, so results are deterministic and testable.
6. **Levers, not judgement.** The agent never says a goal is "unrealistic". It shows the three choices with numbers and lets the user decide.
7. **Same guards as Phase 6.** Pre-formatted number strings in facts; numeric guard, percentage check, claims guard; no lakh/crore/k words.
8. **Archive, don't delete.** A goal's contributions are history the paper and the user may need.

## 6. Reference code (VERIFIED: 27 tests pass)

Copy these files exactly.

**`app/services/goals/goal_math.py`**

```python
# app/services/goals/goal_math.py
#
# Purpose: the money maths of a single goal. Unlike the Phase 6 SIP functions, every function
# here also counts money ALREADY saved for the goal (the "starts from Rs 0" limitation found in
# Phase 6), and targets can be inflated from today's prices to the price on the goal date.
# Pure Python, monthly compounding, contributions at the end of each month.

from datetime import date


def months_between(start: date, end: date) -> int:
    """Whole months from start to end (end's day earlier than start's day counts one fewer). Never negative."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def monthly_rate(annual_return: float) -> float:
    """Monthly rate that compounds to the annual return: (1 + r) ** (1/12) - 1."""
    if annual_return <= -1:
        raise ValueError("annual_return must be greater than -100%")
    return (1 + annual_return) ** (1 / 12) - 1


def inflate(amount_today: float, annual_inflation: float, months: int) -> float:
    """What something costing `amount_today` now will cost after `months` months of inflation."""
    if amount_today < 0 or months < 0 or annual_inflation <= -1:
        raise ValueError("amount and months must be >= 0 and inflation > -100%")
    return amount_today * (1 + annual_inflation) ** (months / 12)


def future_value(current_saved: float, monthly: float, annual_return: float, months: int) -> float:
    """Value after `months`: what is saved now grows, plus a flat monthly contribution."""
    if current_saved < 0 or monthly < 0 or months < 0:
        raise ValueError("current_saved, monthly and months must be >= 0")
    rate = monthly_rate(annual_return)
    growth = (1 + rate) ** months
    if rate == 0:
        return current_saved + monthly * months
    return current_saved * growth + monthly * (growth - 1) / rate


def required_monthly(target_future: float, current_saved: float, annual_return: float, months: int) -> float:
    """
    Flat monthly contribution needed to reach `target_future` in `months`, after letting the
    money already saved grow. 0 when the saved money alone is enough.
    """
    if months <= 0:
        raise ValueError("months must be at least 1 (the goal date has passed or is this month)")
    gap = target_future - future_value(current_saved, 0.0, annual_return, months)
    if gap <= 0:
        return 0.0
    unit = future_value(0.0, 1.0, annual_return, months)    # value of Rs 1 a month
    return gap / unit


def months_to_target(target_future: float, current_saved: float, monthly: float, annual_return: float,
                     max_months: int = 1200) -> int | None:
    """Months until the balance reaches the target; None if it never does within max_months."""
    if current_saved >= target_future:
        return 0
    if monthly <= 0 and annual_return <= 0:
        return None
    rate = monthly_rate(annual_return)
    balance = current_saved
    for month in range(1, max_months + 1):
        balance = balance * (1 + rate) + monthly
        if balance >= target_future:
            return month
    return None
```

**`app/services/goals/goal_allocator.py`**

```python
# app/services/goals/goal_allocator.py
#
# Purpose: share ONE affordable monthly amount (the Phase 6 SIP capacity) between several goals.
# The proposed rule is a priority WATERFALL: essential goals first, then important, then
# aspirational; inside a priority the nearest deadline goes first. Each goal is funded in full
# before the next one gets anything. Two baselines (proportional and equal split) exist so the
# paper can compare them. Pure Python, deterministic (ties broken by goal_id).

from dataclasses import dataclass

PRIORITY_RANK = {"essential": 0, "important": 1, "aspirational": 2}
FULLY, PARTIAL, UNFUNDED, NOT_NEEDED = "fully_funded", "partially_funded", "unfunded", "not_needed"


@dataclass(frozen=True)
class GoalNeed:
    goal_id: int
    priority: str               # 'essential' | 'important' | 'aspirational'
    months_left: int
    required_monthly: float     # from goal_math.required_monthly

    def __post_init__(self) -> None:
        if self.priority not in PRIORITY_RANK:
            raise ValueError(f"unknown priority: {self.priority}")
        if self.required_monthly < 0 or self.months_left < 0:
            raise ValueError("required_monthly and months_left must be >= 0")


@dataclass(frozen=True)
class GoalAllocation:
    goal_id: int
    required_monthly: float
    assigned_monthly: float
    funded_fraction: float      # 0..1
    status: str


def _status(required: float, assigned: float) -> str:
    if required == 0:
        return NOT_NEEDED
    if assigned >= required - 0.005:
        return FULLY
    return PARTIAL if assigned > 0 else UNFUNDED


def _result(need: GoalNeed, assigned: float) -> GoalAllocation:
    assigned = round(min(assigned, need.required_monthly), 2)
    fraction = 1.0 if need.required_monthly == 0 else min(1.0, assigned / need.required_monthly)
    return GoalAllocation(need.goal_id, round(need.required_monthly, 2), assigned, round(fraction, 4),
                          _status(need.required_monthly, assigned))


def priority_order(needs: list[GoalNeed]) -> list[GoalNeed]:
    return sorted(needs, key=lambda n: (PRIORITY_RANK[n.priority], n.months_left, n.goal_id))


def allocate_waterfall(needs: list[GoalNeed], capacity: float) -> list[GoalAllocation]:
    """Proposed method. Returns allocations in priority order."""
    if capacity < 0:
        raise ValueError("capacity must be >= 0")
    remaining, out = capacity, []
    for need in priority_order(needs):
        assigned = min(need.required_monthly, remaining)
        remaining -= assigned
        out.append(_result(need, assigned))
    return out


def allocate_proportional(needs: list[GoalNeed], capacity: float) -> list[GoalAllocation]:
    """Baseline: every goal gets the same fraction of what it needs."""
    if capacity < 0:
        raise ValueError("capacity must be >= 0")
    total = sum(n.required_monthly for n in needs)
    share = 1.0 if total <= capacity or total == 0 else capacity / total
    return [_result(n, n.required_monthly * share) for n in priority_order(needs)]


def allocate_equal(needs: list[GoalNeed], capacity: float) -> list[GoalAllocation]:
    """
    Baseline: split equally between the goals that need money; any amount a goal cannot use
    is re-shared among the rest (so no capacity is wasted).
    """
    if capacity < 0:
        raise ValueError("capacity must be >= 0")
    assigned = {n.goal_id: 0.0 for n in needs}
    open_needs = [n for n in needs if n.required_monthly > 0]
    remaining = capacity
    while open_needs and remaining > 0.005:
        share = remaining / len(open_needs)
        still_open = []
        for need in open_needs:
            give = min(share, need.required_monthly - assigned[need.goal_id])
            assigned[need.goal_id] += give
            remaining -= give
            if need.required_monthly - assigned[need.goal_id] > 0.005:
                still_open.append(need)
        if len(still_open) == len(open_needs):     # everyone took a full share, nothing left to re-share
            break
        open_needs = still_open
    return [_result(n, assigned[n.goal_id]) for n in priority_order(needs)]
```

**`app/services/goals/goal_levers.py`**

```python
# app/services/goals/goal_levers.py
#
# Purpose: when a goal is not fully funded, show the user the three honest choices instead of
# just "you will miss it": (1) add this much per month, (2) move the date to here, or
# (3) aim for this smaller amount by the original date. Also the "raise your SIP by Rs X to
# finish N months earlier" sentence from the project PDF. Pure Python on top of goal_math.

from dataclasses import dataclass

from app.services.goals.goal_math import future_value, months_to_target, required_monthly


@dataclass(frozen=True)
class GoalLevers:
    extra_monthly_needed: float           # add this to the assigned amount to finish on time
    months_needed_at_assigned: int | None  # how long it takes at the assigned amount (None = never within 100 years)
    delay_months: int | None              # months_needed - original months (None = never)
    achievable_by_date: float             # what the assigned amount reaches by the original date


def levers(target_future: float, current_saved: float, assigned_monthly: float,
           annual_return: float, months_left: int) -> GoalLevers:
    need = required_monthly(target_future, current_saved, annual_return, months_left)
    months_needed = months_to_target(target_future, current_saved, assigned_monthly, annual_return)
    return GoalLevers(
        extra_monthly_needed=round(max(0.0, need - assigned_monthly), 2),
        months_needed_at_assigned=months_needed,
        delay_months=None if months_needed is None else max(0, months_needed - months_left),
        achievable_by_date=round(future_value(current_saved, assigned_monthly, annual_return, months_left), 2),
    )


def months_saved_by_raising(target_future: float, current_saved: float, base_monthly: float,
                            extra_monthly: float, annual_return: float) -> int | None:
    """How many months sooner the goal is reached if the monthly amount rises by extra_monthly."""
    if extra_monthly < 0:
        raise ValueError("extra_monthly must be >= 0")
    before = months_to_target(target_future, current_saved, base_monthly, annual_return)
    after = months_to_target(target_future, current_saved, base_monthly + extra_monthly, annual_return)
    if after is None:
        return None
    if before is None:
        return None          # it was never reached before, so "months earlier" has no meaning
    return before - after
```

**`app/services/goals/goal_simulation.py`**

```python
# app/services/goals/goal_simulation.py
#
# Purpose: chance of reaching ONE goal, including money already saved (the Phase 6 Monte Carlo
# started from zero). Same seeded normal-returns model as Phase 6 so results are comparable;
# the return/volatility come from the goal's own allocation (shorter goals are safer).
# Pure Python, reproducible.

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class GoalSimulation:
    p10: float
    p50: float
    p90: float
    probability_of_target: float
    n_paths: int


def _percentile(sorted_values: list[float], q: float) -> float:
    position = q * (len(sorted_values) - 1)
    lower, upper = int(position), min(int(position) + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def simulate_goal(current_saved: float, monthly: float, months: int, mean_annual_return: float,
                  annual_volatility: float, target_future: float, n_paths: int = 2000, seed: int = 42) -> GoalSimulation:
    if n_paths <= 0 or months < 0 or annual_volatility < 0 or monthly < 0 or current_saved < 0:
        raise ValueError("invalid simulation inputs")
    rng = random.Random(seed)
    mean_monthly = (1 + mean_annual_return) ** (1 / 12) - 1
    sigma_monthly = annual_volatility / math.sqrt(12)
    finals: list[float] = []
    for _ in range(n_paths):
        balance = current_saved
        for _month in range(months):
            balance = balance * (1 + max(-0.99, rng.gauss(mean_monthly, sigma_monthly))) + monthly
        finals.append(balance)
    finals.sort()
    hits = sum(1 for value in finals if value >= target_future)
    return GoalSimulation(round(_percentile(finals, 0.1), 2), round(_percentile(finals, 0.5), 2),
                          round(_percentile(finals, 0.9), 2), hits / n_paths, n_paths)
```

**`app/services/goals/progress_tracker.py`**

```python
# app/services/goals/progress_tracker.py
#
# Purpose: answer "am I on track?" for a goal. Compares what the user has actually saved with
# where the plan said they would be by now (deterministic path at the assumed return). A small
# tolerance band avoids calling someone "behind" over a few rupees. Pure Python.

from dataclasses import dataclass

from app.services.goals.goal_math import future_value

AHEAD, ON_TRACK, BEHIND = "ahead", "on_track", "behind"


@dataclass(frozen=True)
class Progress:
    expected_balance: float
    actual_balance: float
    gap: float                 # actual - expected (negative means behind)
    status: str
    percent_of_target: float   # actual / target_future, 0..1+


def track(saved_at_plan_start: float, planned_monthly: float, annual_return: float, months_elapsed: int,
          actual_balance: float, target_future: float, tolerance: float = 0.05) -> Progress:
    """
    tolerance is a fraction of the expected balance (0.05 = within 5% counts as on track).
    With nothing expected yet (start of plan, zero balance) the user is on track.
    """
    if target_future <= 0 or tolerance < 0 or actual_balance < 0:
        raise ValueError("target must be > 0; tolerance and balance must be >= 0")
    expected = future_value(saved_at_plan_start, planned_monthly, annual_return, months_elapsed)
    gap = actual_balance - expected
    band = tolerance * expected
    if expected == 0 or abs(gap) <= band:
        status = ON_TRACK
    else:
        status = AHEAD if gap > 0 else BEHIND
    return Progress(round(expected, 2), round(actual_balance, 2), round(gap, 2), status,
                    round(actual_balance / target_future, 4))
```


### Tests (copy into backend/tests/)

**`tests/test_goal_math.py`**

```python
# tests/test_goal_math.py

from datetime import date

import pytest

from app.services.goals.goal_math import (
    future_value, inflate, months_between, months_to_target, required_monthly,
)


def test_months_between_counts_whole_months_and_never_goes_negative():
    assert months_between(date(2026, 11, 13), date(2027, 11, 13)) == 12
    assert months_between(date(2026, 11, 13), date(2027, 11, 12)) == 11
    assert months_between(date(2027, 1, 1), date(2026, 1, 1)) == 0


def test_inflation_compounds_yearly_rate_over_months():
    assert inflate(100_000, 0.06, 24) == pytest.approx(100_000 * 1.06 ** 2)
    assert inflate(100_000, 0.0, 60) == 100_000


def test_future_value_with_zero_return_is_saved_plus_deposits():
    assert future_value(10_000, 1_000, 0.0, 12) == pytest.approx(22_000)


def test_future_value_counts_existing_savings_growth():
    assert future_value(100_000, 0, 0.10, 12) == pytest.approx(110_000)


def test_required_monthly_round_trips_with_future_value():
    need = required_monthly(1_000_000, 50_000, 0.08, 60)
    assert future_value(50_000, need, 0.08, 60) == pytest.approx(1_000_000, rel=1e-9)


def test_existing_savings_reduce_the_monthly_need_and_can_remove_it():
    assert required_monthly(500_000, 100_000, 0.08, 36) < required_monthly(500_000, 0, 0.08, 36)
    assert required_monthly(100_000, 200_000, 0.05, 12) == 0.0


def test_goal_date_in_the_past_is_rejected():
    with pytest.raises(ValueError):
        required_monthly(100_000, 0, 0.08, 0)


def test_months_to_target_matches_required_monthly():
    need = required_monthly(300_000, 20_000, 0.07, 40)
    assert months_to_target(300_000, 20_000, need + 0.01, 0.07) == 40


def test_months_to_target_edge_cases():
    assert months_to_target(100, 200, 0, 0.05) == 0
    assert months_to_target(100_000, 0, 0, 0.0) is None
    assert months_to_target(10**12, 0, 1, 0.01) is None
```

**`tests/test_goal_allocator.py`**

```python
# tests/test_goal_allocator.py

import pytest

from app.services.goals.goal_allocator import (
    FULLY, NOT_NEEDED, PARTIAL, UNFUNDED, GoalNeed, allocate_equal, allocate_proportional, allocate_waterfall,
)

NEEDS = [
    GoalNeed(1, "aspirational", 24, 6_000),     # vacation
    GoalNeed(2, "essential", 60, 8_000),        # house down payment
    GoalNeed(3, "important", 120, 5_000),       # child education
    GoalNeed(4, "essential", 12, 3_000),        # emergency top-up style goal, nearest deadline
]


def by_id(rows):
    return {row.goal_id: row for row in rows}


def test_waterfall_funds_essentials_first_nearest_deadline_first():
    rows = allocate_waterfall(NEEDS, 12_000)
    assert [r.goal_id for r in rows] == [4, 2, 3, 1]
    got = by_id(rows)
    assert got[4].status == FULLY and got[2].status == FULLY
    assert got[3].status == PARTIAL and got[3].assigned_monthly == pytest.approx(1_000)
    assert got[1].status == UNFUNDED


def test_waterfall_never_assigns_more_than_capacity_or_need():
    for capacity in (0, 5_000, 12_000, 50_000):
        rows = allocate_waterfall(NEEDS, capacity)
        assert sum(r.assigned_monthly for r in rows) <= capacity + 0.01
        assert all(r.assigned_monthly <= r.required_monthly + 0.01 for r in rows)


def test_enough_capacity_funds_everything_in_every_method():
    for method in (allocate_waterfall, allocate_proportional, allocate_equal):
        assert all(r.status == FULLY for r in method(NEEDS, 30_000))


def test_proportional_gives_everyone_the_same_fraction():
    fractions = {r.funded_fraction for r in allocate_proportional(NEEDS, 11_000)}
    assert len(fractions) == 1 and fractions.pop() == pytest.approx(0.5)


def test_equal_split_reshares_what_a_small_goal_cannot_use():
    rows = by_id(allocate_equal(NEEDS, 16_000))
    assert rows[4].status == FULLY                      # needs only 3,000 of its 4,000 share
    assert sum(r.assigned_monthly for r in rows.values()) == pytest.approx(16_000, abs=0.05)


def test_goal_that_needs_nothing_is_not_needed():
    rows = allocate_waterfall([GoalNeed(9, "important", 10, 0.0)], 1_000)
    assert rows[0].status == NOT_NEEDED and rows[0].funded_fraction == 1.0


def test_invalid_priority_and_capacity_are_rejected():
    with pytest.raises(ValueError):
        GoalNeed(1, "urgent", 10, 100)
    with pytest.raises(ValueError):
        allocate_waterfall(NEEDS, -1)
```

**`tests/test_goal_levers_and_progress.py`**

```python
# tests/test_goal_levers_and_progress.py

import pytest

from app.services.goals.goal_levers import levers, months_saved_by_raising
from app.services.goals.goal_math import future_value, required_monthly
from app.services.goals.progress_tracker import AHEAD, BEHIND, ON_TRACK, track


def test_fully_funded_goal_has_no_extra_need_and_no_delay():
    need = required_monthly(600_000, 0, 0.08, 48)
    result = levers(600_000, 0, need + 1, 0.08, 48)
    assert result.extra_monthly_needed == 0 and result.delay_months == 0


def test_underfunded_goal_shows_all_three_levers_consistently():
    need = required_monthly(600_000, 0, 0.08, 48)
    result = levers(600_000, 0, need / 2, 0.08, 48)
    assert result.extra_monthly_needed == pytest.approx(need / 2, abs=0.01)
    assert result.delay_months > 0
    assert result.achievable_by_date == pytest.approx(future_value(0, need / 2, 0.08, 48), abs=0.01)
    assert result.achievable_by_date < 600_000


def test_zero_assigned_and_zero_return_never_reaches():
    result = levers(100_000, 0, 0, 0.0, 24)
    assert result.months_needed_at_assigned is None and result.delay_months is None


def test_raising_the_sip_finishes_earlier():
    saved = months_saved_by_raising(1_000_000, 0, 10_000, 2_000, 0.10)
    assert saved is not None and saved > 0
    assert months_saved_by_raising(1_000_000, 0, 10_000, 0, 0.10) == 0


def test_on_track_within_tolerance_ahead_and_behind_outside_it():
    expected = future_value(10_000, 5_000, 0.08, 12)
    assert track(10_000, 5_000, 0.08, 12, expected * 1.02, 500_000).status == ON_TRACK
    assert track(10_000, 5_000, 0.08, 12, expected * 1.20, 500_000).status == AHEAD
    assert track(10_000, 5_000, 0.08, 12, expected * 0.80, 500_000).status == BEHIND


def test_start_of_plan_with_nothing_expected_is_on_track():
    result = track(0, 5_000, 0.08, 0, 0, 100_000)
    assert result.status == ON_TRACK and result.percent_of_target == 0


def test_progress_rejects_bad_target():
    with pytest.raises(ValueError):
        track(0, 1_000, 0.08, 3, 100, 0)
```

**`tests/test_goal_simulation.py`**

```python
# tests/test_goal_simulation.py

import pytest

from app.services.goals.goal_math import future_value
from app.services.goals.goal_simulation import simulate_goal


def test_same_seed_same_result():
    assert simulate_goal(50_000, 5_000, 36, 0.09, 0.12, 300_000, n_paths=200, seed=1) == \
        simulate_goal(50_000, 5_000, 36, 0.09, 0.12, 300_000, n_paths=200, seed=1)


def test_zero_volatility_matches_the_closed_form_including_existing_savings():
    result = simulate_goal(50_000, 5_000, 36, 0.09, 0.0, 300_000, n_paths=10)
    assert result.p50 == pytest.approx(future_value(50_000, 5_000, 0.09, 36), rel=1e-6)


def test_existing_savings_raise_the_chance_of_success():
    without = simulate_goal(0, 5_000, 36, 0.09, 0.12, 250_000, n_paths=500).probability_of_target
    with_savings = simulate_goal(50_000, 5_000, 36, 0.09, 0.12, 250_000, n_paths=500).probability_of_target
    assert with_savings > without


def test_percentiles_are_ordered_and_bad_inputs_rejected():
    result = simulate_goal(10_000, 2_000, 24, 0.08, 0.15, 80_000, n_paths=300)
    assert result.p10 <= result.p50 <= result.p90
    with pytest.raises(ValueError):
        simulate_goal(-1, 2_000, 24, 0.08, 0.15, 80_000)
```


## 7. Code explanation (beginner level)

- **`goal_math.py`**: `months_between` counts whole months to the goal date. `inflate` grows today's price to the future price. `future_value` = savings that grow + a monthly deposit that grows (the standard annuity formula). `required_monthly` subtracts what your savings will grow to, then divides the rest by the value of "Rs 1 a month". `months_to_target` simulates month by month until the target is reached.
- **`goal_allocator.py`**: `allocate_waterfall` sorts goals by priority and deadline, then fills each one completely before moving on, like pouring water into glasses in order. The two baselines give every goal the same fraction (`proportional`) or the same rupees (`equal`, re-sharing what a small goal cannot use).
- **`goal_levers.py`**: for a goal that is short of money, it calculates the extra monthly amount needed, how late it would be at the current amount, and what you would have by the original date.
- **`goal_simulation.py`**: like the Phase 6 Monte Carlo, but the balance starts from the money already saved.
- **`progress_tracker.py`**: compares your real balance with where the plan expected you to be, with a 5% "close enough" band.

## 8. Contracts for files still to build

### `goal_service.py`
- `create_goal`, `update_goal`, `archive_goal`, `add_contribution`, `live_balance(goal)`.
- `generate_plan(db, user_id, goal_overrides=None)`:
  1. Load active goals (skip achieved and archived). No active goals: 409 "add a goal first".
  2. Load the Phase 6 profile (category) and the latest investment plan (capacity, emergency top-up). Missing: 409.
  3. For each goal: months left (goal date in the past: warning `goal_overdue` and the goal is left out of the allocation), inflated target, allocation for its horizon, return and volatility, required monthly from the live balance.
  4. `allocate_waterfall`, then levers for partial and unfunded goals, `months_saved_by_raising` with a settings-defined example step (for example Rs 1,000), and `simulate_goal` for each goal with the goal's own return and volatility.
  5. Build facts (aggregates only, pre-formatted strings), call the explainer, store the plan.
- `progress(db, user_id, goal_id)`: uses the latest plan's assigned monthly and return for that goal; no plan yet: status `no_plan`.

### `goal_explainer.py`
Same pattern as the investment explainer: one call, facts only, numeric + percentage + claims guards, template fallback, disclaimer added by code. The text must mention which goals are fully funded, which are short and their levers, and that projections are scenarios under assumptions.

### Settings
`GOAL_DEFAULT_INFLATION=0.06`, `GOAL_INFLATION_EDUCATION=0.08`, `GOAL_INFLATION_HOUSE=0.07` (all illustrative placeholders), `GOAL_LIMIT_PER_USER=10`, `GOAL_LEVER_STEP=1000`, `GOAL_MC_PATHS` and seed reuse the INVEST_* values.

## 9. Research plan (IEEE angle)

**Contribution (draft):** a goal-based planner that allocates a forecast-constrained, emergency-first monthly capacity across prioritised goals, with per-goal horizon-aware allocations, inflation and existing savings, explicit trade-off levers, and an evaluation against simple allocation baselines.

### Pre-register first (commit before any evaluation code, same habit as 6B and 7B)
- Metrics and their weights, decided BEFORE results: priority weights essential 3, important 2, aspirational 1.
- Rules, for example: G1 no aspirational goal receives money while an essential goal is short; G2 total assigned never exceeds capacity; G3 a goal under 12 months away has no equity (via the Phase 6 cap); G4 a goal with savings already enough is marked not needed.

### Evaluation set
About 500 synthetic personas (fixed seed), each with 2 to 5 goals across types, priorities, horizons 6 months to 25 years, varied savings, and a capacity drawn relative to total need (from very short to more than enough). State that personas are synthetic and attributes partly independent.

### Methods compared
- Proposed P: waterfall (priority, then deadline).
- B1: proportional split. B2: equal split. B3: deadline-only waterfall (ignores priority).

### Metrics
1. Essential goals fully funded (rate).
2. Priority-weighted expected attainment: weighted mean of min(1, achievable_by_date / target) across goals.
3. Mean success probability by priority class.
4. Number of goals fully funded per persona, and total monthly shortfall.
5. Rule violation rates (G1 to G4).
6. Explanation: faithfulness rate, raw prohibited-claims rate, fallback rate, calls per plan.

### Ablations (quantify common planning mistakes)
- **No inflation:** plan with today's prices, evaluate against inflated targets. Expect over-optimistic success.
- **Ignore existing savings** (the Phase 6 behaviour): expect over-saving and lower apparent feasibility.
- **One allocation for all goals** instead of per-goal horizon allocation: expect more short-term goals exposed to equity.
- Normal Monte Carlo vs the Phase 7 historical bootstrap for success probability (optional, if Phase 7 history is long enough).

### Limitations to state
Constant assumed returns and inflation per goal; synthetic personas; flat monthly contributions (no step-up in Phase 8); allocation fixed per plan run (the plan must be regenerated as horizons shorten); no taxes or fees.

## 10. Implementation steps for Claude Code (gated prompts)

Stop after each prompt and report. Use Plan mode.

### Prompt 8A-0: audit and branch
```text
Read CLAUDE.md and docs/phase8-spec.md. Confirm Phase 7 (feature/vishesh-stocks) is merged into DEV (git fetch origin; git branch -r --merged origin/DEV) and that I am on feature/vishesh-goals cut from the updated DEV. Then audit and report, no code yet: (1) how to read the latest stored investment plan's capacity_total, emergency top-up and the profile category, with exact names; (2) how to call target_allocation and portfolio_return_and_volatility with the current INVEST_* assumptions from a service; (3) the explainer and guard functions the investment explainer uses, so the goal explainer can reuse them; (4) the latest migration id; (5) current test count. List anything in docs/phase8-spec.md that does not fit the code. Wait for my go-ahead.
```

### Prompt 8A-1: pure modules
```text
Create backend/app/services/goals/ with the five modules from section 6 of docs/phase8-spec.md and the four test files, copied EXACTLY, plus __init__.py. Run docker compose exec backend python -m pytest tests -q. Expect 27 new tests on top of the existing ones. No logic changes. Commit and report totals.
```

### Prompt 8A-2: data model
```text
Add models/goal.py (Goal, GoalContribution, GoalPlan), schemas/goal.py and Alembic migration 0007_goals.py following the latest migration, exactly as in section 4 (tables) of the spec: Numeric for money, CHECK constraints for goal_type, priority and status, the indexes, cascade on contributions. Literal types in schemas; the target date must be at least one month away; the per-user goal limit. Test that the migration applies, downgrades and upgrades again. Register the models. Report.
```

### Prompt 8A-3: settings and service
```text
Add the GOAL_* settings from section 8 to core/config.py and backend/.env.example, with a comment that the inflation values are placeholders to cite before publication. Create services/goal_service.py per section 8 and decisions 1 to 5 in section 5. Every query filters by user_id. Missing investment plan or no active goals give 409 with a clear message; goal_overdue and capacity_stale warnings; achieved and archived goals skipped. Service tests: priority order respected, inflation applied, saved money reduces the need, capacity never exceeded, overdue goal excluded with a warning, user isolation, emergency fund not counted twice. Report.
```

### Prompt 8A-4: explainer
```text
Create agents/goal_explainer.py reusing the investment explainer's guards and pattern (one LLM call through the provider abstraction, facts only with pre-formatted strings, numeric + percentage + claims guards, no lakh/crore/k, template fallback, disclaimer added by code, store llm_used, explanation_faithful, claims_compliant, llm_calls, raw_prohibited_claims). Tests with a stub LLM as in section 4 (testing strategy, item 3). Report.
```

### Prompt 8A-5: routes
```text
Create api/routes/goals.py with every endpoint in section 4 (API design), JWT on all, user_id only from the token, disclaimer in every response, DELETE archives instead of deleting. Route tests: auth, user isolation (goals, contributions, plans, progress), validation, 404 for another user's plan. Extend scripts/smoke_test.py with one goals step. Run the full suite and the smoke test, commit, report totals. Do not push.
```

### Prompt 8B: evaluation and docs
```text
First write evaluation/goals_rules.py with the pre-registered metrics, weights and rules G1 to G4 from section 9 and tell me to commit it BEFORE any evaluation code. Then write the persona generator, methods P, B1, B2, B3, the ablations and evaluation/evaluate_goals.py (flat style, writes goals_metrics.csv and .md), plus a consistency test that the real generate_plan matches the harness's P on about 20 personas. Keep a "Changes after seeing results" table with at most one round of fixes. Write docs/modules/goals.md and goals-evaluation.md, update api.md, database.md, testing.md, architecture.md and CLAUDE.md. Report the key numbers honestly.
```

### Prompt 8C: frontend
```text
Add frontend/app/(console)/goals/page.tsx in the existing console style (Tailwind, Shadcn UI, Recharts only) and a nav link. Sections: (1) goal list with live balance, progress bar and status badge, plus create/edit/archive; (2) add contribution; (3) the Phase 6 safety-net row (read-only emergency top-up); (4) Generate plan, with clear 409 messages that point to /investing; (5) plan results: capacity used and left, a table per goal (target today, inflated target, months left, required, assigned, status), a stacked bar of how the monthly amount is shared, levers for short goals in plain sentences, success probability with the bad/typical/good range, warnings, explanation with the AI-written/Template badge, plan history; (6) what-if per goal. Permanent banner: "Educational planning tool, not financial advice. Projections are scenarios under stated assumptions, not guarantees." Types for every response, NEXT_PUBLIC_API_URL only, Indian rupee formatting, empty/loading/error states, responsive. npm run lint and npm run build must pass. Do not push.
```

## 11. Common beginner mistakes
- Forgetting inflation: Rs 10 lakh today is far more in 10 years, and the plan looks fine when it is not.
- Ignoring money already saved, which makes every goal look harder than it is.
- Counting the emergency fund twice (Phase 6 already reserves it).
- Investing a 6-month goal in equity.
- Storing the live balance AND the contributions, then letting them disagree.
- Letting the SIP split exceed the capacity because of rounding: always check the sum in tests.
- Hard-deleting a goal and losing its contribution history.

## 12. Testing steps
1. `docker compose exec backend python -m pytest tests -q`
2. `docker compose exec backend python -m scripts.smoke_test`
3. In the app: generate an investment plan first, then create three goals (an essential house goal in 5 years, an important education goal in 12 years, an aspirational vacation in 1 year), log a contribution and generate a goal plan. Check that the essential goal is funded first, the vacation has no equity, and the short goal shows levers.

## 13. Next steps
Phase 9 (Automation Agent) schedules data refresh, monthly plan regeneration and reminders ("your house goal is behind by Rs X") with Celery and Redis.
