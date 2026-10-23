# Phase 6 Spec: Investment Advisor Agent

Status: design + VERIFIED reference code (65 unit tests pass). Build on a branch cut from the updated `DEV` AFTER Phase 5 is merged: `feature/vishesh-investing`.
Everything here is an **educational planning tool, not financial advice**. Every API response and every screen must carry the disclaimer.

---

## 1. Overview

The Investment Advisor answers four questions for one user, using only their own data:

1. **How much risk should I take?** A risk profile from a short questionnaire (tolerance) AND from their finances (capacity). The safer of the two wins.
2. **How should I split my money?** A target split across four asset classes (equity, debt, gold, cash), reduced for short time horizons, compared with what they hold, with rebalancing hints.
3. **How much can I invest each month without breaking my budget?** A SIP size computed from the LOWER band of the Phase 5 forecast surplus, after topping up the emergency fund.
4. **What might happen?** A Monte Carlo range (10th / 50th / 90th percentile) and the probability of reaching a goal, under clearly labelled assumptions.

Design rule inherited from Phases 4 and 5: **Python does all the maths, the LLM only explains.** One LLM call per plan, aggregates only, two guards (numbers + prohibited claims), template fallback.

### What is deliberately NOT in Phase 6
- **No named funds, no stock picks, no live prices.** We recommend asset classes and instrument TYPES (for example "broad-market index fund", "liquid fund"). Specific stocks belong to Phase 7 (Stock Intelligence), which will plug in as an optional "satellite" slice. Naming real funds with returns would be fabricated or stale data and a regulatory risk.
- **No portfolio optimisation (Markowitz).** Listed in the PDF as future scope. We note it as future work; the rule-based allocation is the research baseline for it.
- **No broker integration.** Real returns need real holdings; Phase 3B only sees money going out to and coming back from investment platforms. The user enters current holdings by hand (amount per asset class).

## 2. Why It Is Needed

- Completes Module 4 of the project PDF (risk profiling, diversification, SIP planning, fund/ETF suggestions at category level).
- Connects Phase 3B (investment flows) and Phase 5 (forecast surplus) to a decision: "what should the leftover money do?"
- Gives the paper its strongest novelty: **budget-aware, capacity-gated, explainable investment planning with a compliance guard on LLM text**. Most robo-advisor papers stop at questionnaire-based allocation.

## 3. Folder Location

```text
backend/app/
  services/investing/
    __init__.py
    risk_scoring.py        (reference code below, pure)
    allocation.py          (reference code below, pure)
    portfolio_stats.py     (reference code below, pure)
    sip_planner.py         (reference code below, pure)
    monte_carlo.py         (reference code below, pure)
    claims_guard.py        (reference code below, pure)
    instrument_catalog.py  (static: asset class -> instrument types + plain-language notes)
    assumptions.py         (loads return/volatility assumptions from settings)
  services/investment_service.py   (orchestration: DB + forecast + budget -> plan)
  agents/investment_explainer.py   (ONE LLM call, LCEL, guards, fallback template)
  models/investment.py             (InvestmentProfile, Holding, InvestmentPlan)
  schemas/investment.py
  api/routes/investing.py
backend/migrations/versions/0005_investing.py
backend/evaluation/investing/        (synthetic personas, baselines, ablations)
backend/tests/test_investing_*.py
frontend/app/investing/page.tsx
docs/phase6-spec.md, docs/investing.md
```

The six pure modules have **no imports from the rest of the app** (only each other). That is why they could be tested before any database exists.

## 4. Module Documentation

### Purpose
Turn a person's finances and attitude to risk into an explainable, conservative investment plan.

### Architecture and flow

```text
 questionnaire + facts ──► risk_scoring ──► category (safer of tolerance / capacity)
 horizon_years ───────────►                          │
                                                      ▼
 holdings ───────────────► allocation.target_allocation ──► drift / rebalance rows
                                                      │
 Phase 5 forecast (low band surplus) ─► sip_planner.recommend_sip ─► SIP amount, emergency top-up
 Phase 4 budget (essentials) ─► emergency months                   │
                                                      ▼
 assumptions ─► portfolio_stats ─► monte_carlo.simulate_sip ─► p10 / p50 / p90, P(goal)
                                                      │
                          ALL NUMBERS ──► investment_explainer (1 LLM call)
                                              │ numeric guard + claims guard
                                              ▼  fail => template text
                                       InvestmentPlan row (stored) ──► API ──► UI
```

### Input
- Questionnaire: three answers 1 to 5 (reaction to a 20% fall, experience, return vs safety).
- Facts: liquid savings, income stability 1 to 5, dependents, horizon in years, optional goal amount, existing monthly SIP.
- Holdings: rupees per asset class (manual).
- From other phases: latest forecast run (lower band monthly surplus), active budget (essential spend, used for emergency fund months).

### Output (one stored plan)
- Risk profile: scores, categories, limiting factor.
- Target allocation, current allocation, drift rows with rupee changes.
- SIP: capacity, recommendation, change versus existing, emergency top-up.
- Projection: p10 / p50 / p90, probability of goal, months to goal at current vs recommended SIP.
- Instrument types per asset class (from the static catalog).
- Assumptions used (so the user can see and challenge them), warnings, disclaimer, explanation text, `explanation_source` (`llm` or `template`).

### Database tables (migration 0005)
- `investment_profiles`: id, user_id (FK, unique), tolerance_answers JSON, capacity_facts JSON, tolerance_score, capacity_score, category, limiting_factor, updated_at.
- `holdings`: id, user_id (FK), asset_class (enum of 4), amount Numeric(14,2), label (free text, optional), as_of date. Unique (user_id, asset_class, label).
- `investment_plans`: id, user_id (FK), created_at, inputs_json, result_json, explanation, explanation_source, assumptions_json, engine_version.
- Every query filters by `user_id` from the JWT. Never from the body.

### API design (all under `/api/investing`, JWT required)

| Method | Path | Purpose |
|---|---|---|
| PUT | `/profile` | Save questionnaire + facts, returns the computed risk profile |
| GET | `/profile` | Read it |
| PUT | `/holdings` | Replace the holdings list |
| GET | `/holdings` | Read holdings with current weights |
| POST | `/plan` | Generate a plan (optional `goal_amount`, `goal_years`); stores and returns it |
| GET | `/plan/latest` | Most recent plan |
| GET | `/plans` | History (for the frontend and the paper) |
| POST | `/simulate` | What-if: SIP, years, allocation category, goal. NOT stored |
| GET | `/assumptions` | The return/volatility assumptions currently in force |

### Testing strategy
1. Unit tests on the six pure modules (already written, 65 passing).
2. Service tests with a seeded user and a stubbed forecast.
3. Agent tests with a stub LLM: good text accepted, invented rupee amount rejected, "guaranteed returns" rejected, LLM failure gives template.
4. Route tests: auth, user isolation (user B cannot see user A's plan), disclaimer present.
5. Smoke test extended by one step (profile, holdings, plan, simulate).
6. Evaluation scripts (section 9).

## 5. Key design decisions (read these, they are examples of good engineering judgement)

1. **Tolerance vs capacity.** Questionnaires measure attitude. They cannot see that someone has no emergency fund. We score both and take the safer category. `limiting_factor` is returned so the explanation can say *why* ("your finances, not your comfort with risk, are the limit right now"). This is also a clean ablation.
2. **Emergency fund before investing.** Under 3 months of essentials, capacity is capped (`CAPACITY_CAP_WITHOUT_EMERGENCY_FUND`), and the SIP recommendation first reserves a monthly top-up.
3. **Lower band, not the average.** The SIP is sized from the forecast's lower surplus band times a safety factor (0.8). This ties directly to the Phase 5 interval work. We know the interval coverage is only 63% against an 80% nominal, so we keep the safety factor and say so in the limitations.
4. **Horizon glide.** Short horizons cap equity (`equity_cap_by_horizon`), excess moves to debt, or to cash under a year.
5. **Drift threshold.** Rebalancing is only suggested when a class is more than 5 percentage points off target, so the advisor does not nag.
6. **Assumptions are data, not code.** Returns and volatilities live in settings, are returned by `/assumptions`, and are shown in the UI. The defaults are **illustrative placeholders** (see `assumptions.py` below). Before the paper is written they must be replaced with figures from a cited source, and a sensitivity table must show results under low / base / high assumptions.
7. **Two guards on LLM text.** The Phase 4 numeric guard (no rupee amount that is not in the computed numbers) plus `claims_guard` (no guarantees, no "risk-free"). The guard is deliberately strict: it also flags a negation such as "not guaranteed", which triggers the template. Measuring that false-positive rate is itself a small finding.
8. **Seeded simulation.** Same inputs, same seed, same answer. The plan stores the seed so a result can be reproduced exactly.

## 6. Reference code (VERIFIED: 65 tests pass)

Copy these files exactly. Do not "improve" the maths without updating the tests.

**`app/services/investing/risk_scoring.py`**

```python
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
```

**`app/services/investing/allocation.py`**

```python
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
```

**`app/services/investing/portfolio_stats.py`**

```python
# app/services/investing/portfolio_stats.py
#
# Purpose: combine per-asset-class assumptions (expected return, volatility) and a split into
# one portfolio-level return and volatility, so the Monte Carlo can simulate the whole plan.
# All numbers are ASSUMPTIONS supplied by configuration (never hard-coded in logic) and are
# shown to the user as assumptions. Pure Python.

import math
from dataclasses import dataclass

from app.services.investing.allocation import ASSET_CLASSES


@dataclass(frozen=True)
class AssetAssumption:
    expected_return: float   # annual, 0.10 = 10%
    volatility: float        # annual standard deviation


# Pairwise correlation assumptions between asset classes (symmetric). Missing pairs mean 0.
DEFAULT_CORRELATIONS: dict[frozenset, float] = {
    frozenset(("equity", "debt")): 0.10,
    frozenset(("equity", "gold")): 0.05,
    frozenset(("equity", "cash")): 0.00,
    frozenset(("debt", "gold")): 0.10,
    frozenset(("debt", "cash")): 0.30,
    frozenset(("gold", "cash")): 0.00,
}


def _correlation(a: str, b: str, correlations: dict[frozenset, float]) -> float:
    return 1.0 if a == b else correlations.get(frozenset((a, b)), 0.0)


def portfolio_return_and_volatility(
    weights: dict[str, float],
    assumptions: dict[str, AssetAssumption],
    correlations: dict[frozenset, float] | None = None,
) -> tuple[float, float]:
    """Weighted mean return, and volatility = sqrt(sum_i sum_j w_i w_j sigma_i sigma_j rho_ij)."""
    correlations = DEFAULT_CORRELATIONS if correlations is None else correlations
    missing = [name for name in ASSET_CLASSES if weights.get(name, 0.0) > 0 and name not in assumptions]
    if missing:
        raise ValueError(f"no assumptions for: {', '.join(missing)}")
    mean = sum(weights.get(n, 0.0) * assumptions[n].expected_return for n in ASSET_CLASSES if n in assumptions)
    variance = 0.0
    for a in ASSET_CLASSES:
        for b in ASSET_CLASSES:
            if a in assumptions and b in assumptions:
                variance += (weights.get(a, 0.0) * weights.get(b, 0.0)
                             * assumptions[a].volatility * assumptions[b].volatility
                             * _correlation(a, b, correlations))
    return mean, math.sqrt(max(variance, 0.0))
```

**`app/services/investing/sip_planner.py`**

```python
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


def recommend_sip(
    surplus_before_investing_low: float, existing_sip: float, emergency_gap: float,
    months_to_fill_emergency: int = 12, safety_factor: float = 0.8, target_total: float | None = None,
) -> SipRecommendation:
    """
    surplus_before_investing_low: lower-band monthly surplus BEFORE any investing (from the forecast).
    emergency_gap: rupees still missing from the emergency fund (0 if complete).
    The emergency fund is topped up first, then a safety factor keeps a buffer.
    """
    if months_to_fill_emergency <= 0 or not 0 < safety_factor <= 1:
        raise ValueError("months_to_fill_emergency must be > 0 and safety_factor in (0, 1]")
    topup = emergency_gap / months_to_fill_emergency if emergency_gap > 0 else 0.0
    capacity = max(0.0, surplus_before_investing_low * safety_factor - topup)

    if target_total is not None and target_total < capacity:
        recommended, limited_by = max(0.0, target_total), "target"
    else:
        recommended, limited_by = capacity, ("capacity" if capacity < (target_total or float("inf")) else "none")
    return SipRecommendation(
        capacity_total=round(capacity, 2), recommended_total=round(recommended, 2),
        change_vs_existing=round(recommended - existing_sip, 2), emergency_topup=round(topup, 2),
        limited_by=limited_by,
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
```

**`app/services/investing/monte_carlo.py`**

```python
# app/services/investing/monte_carlo.py
#
# Purpose: show a RANGE of outcomes instead of one number. Simulates many possible return
# paths for a SIP and reports the 10th/50th/90th percentile corpus and the chance of
# reaching a goal. Seeded so results are reproducible (needed for tests and the paper).
# Pure Python (random + math). The return/volatility inputs are assumptions supplied by the
# caller, never invented here.

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class SimulationResult:
    p10: float
    p50: float
    p90: float
    probability_of_goal: float | None   # None when no goal was given
    n_paths: int


def _percentile(sorted_values: list[float], q: float) -> float:
    position = q * (len(sorted_values) - 1)
    lower, upper = int(position), min(int(position) + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def simulate_sip(
    monthly_sip: float, years: float, mean_annual_return: float, annual_volatility: float,
    n_paths: int = 2000, seed: int = 42, goal: float | None = None, annual_step_up: float = 0.0,
) -> SimulationResult:
    """Monthly returns are normal with the given mean and volatility (volatility / sqrt(12))."""
    if n_paths <= 0 or annual_volatility < 0 or monthly_sip < 0:
        raise ValueError("n_paths must be > 0; volatility and SIP must be >= 0")
    rng = random.Random(seed)
    mean_monthly = (1 + mean_annual_return) ** (1 / 12) - 1
    sigma_monthly = annual_volatility / math.sqrt(12)
    months = int(round(years * 12))

    finals: list[float] = []
    for _ in range(n_paths):
        balance, contribution = 0.0, monthly_sip
        for month in range(1, months + 1):
            monthly_return = max(-0.99, rng.gauss(mean_monthly, sigma_monthly))   # a month cannot lose more than 99%
            balance = balance * (1 + monthly_return) + contribution
            if month % 12 == 0:
                contribution *= 1 + annual_step_up
        finals.append(balance)

    finals.sort()
    probability = None if goal is None else sum(1 for value in finals if value >= goal) / n_paths
    return SimulationResult(
        p10=round(_percentile(finals, 0.10), 2), p50=round(_percentile(finals, 0.50), 2),
        p90=round(_percentile(finals, 0.90), 2), probability_of_goal=probability, n_paths=n_paths,
    )
```

**`app/services/investing/claims_guard.py`**

```python
# app/services/investing/claims_guard.py
#
# Purpose: compliance-style safety net for LLM-written investment text. The numeric guard from
# Phase 4 catches invented rupee amounts; this one catches promises and absolutes that an
# educational tool must never make ("guaranteed returns", "risk-free"). If either guard fails,
# the caller discards the LLM text and uses the deterministic template instead.
# Also usable as a research metric (how often would the raw LLM output have been unsafe?).

import re

_PROHIBITED = re.compile(
    r"\b(guarantee[sd]?|guaranteed|assured returns?|risk[- ]free|no risk|zero risk|without risk|"
    r"sure[- ]?shot|can'?t lose|cannot lose|will definitely|100% safe|certain to (?:grow|earn|profit))\b",
    re.IGNORECASE,
)


def find_prohibited_claims(text: str) -> list[str]:
    """Returns every prohibited phrase found, lower-cased, in order of appearance."""
    return [match.group(0).lower() for match in _PROHIBITED.finditer(text)]


def is_compliant(text: str) -> bool:
    return not find_prohibited_claims(text)
```


### Tests (copy into backend/tests/)

**`tests/test_risk_scoring.py`**

```python
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
```

**`tests/test_allocation.py`**

```python
# tests/test_allocation.py

import pytest

from app.services.investing.allocation import equity_cap, rebalance_actions, target_allocation


@pytest.mark.parametrize("category", ["conservative", "moderate", "aggressive"])
@pytest.mark.parametrize("horizon", [0.5, 2, 4, 6, 10, 20])
def test_allocation_always_sums_to_one(category, horizon):
    assert sum(target_allocation(category, horizon).values()) == pytest.approx(1.0)


def test_higher_risk_means_more_equity_over_a_long_horizon():
    equity = [target_allocation(c, 15)["equity"] for c in ("conservative", "moderate", "aggressive")]
    assert equity == sorted(equity) and len(set(equity)) == 3


def test_short_horizon_caps_equity():
    assert target_allocation("aggressive", 2)["equity"] == equity_cap(2) == 0.20


def test_under_one_year_has_no_equity_and_excess_goes_to_cash():
    allocation = target_allocation("aggressive", 0.5)
    assert allocation["equity"] == 0.0 and allocation["cash"] > 0.5


def test_unknown_category_is_rejected():
    with pytest.raises(ValueError):
        target_allocation("reckless", 10)


def test_no_flags_when_holdings_match_target():
    target = target_allocation("moderate", 10)
    holdings = {name: 100000 * weight for name, weight in target.items()}
    assert all(row.action == "hold" for row in rebalance_actions(holdings, target))


def test_overweight_equity_is_flagged_to_reduce_and_amounts_balance():
    target = target_allocation("conservative", 10)
    holdings = {"equity": 70000, "debt": 20000, "gold": 5000, "cash": 5000}
    rows = {row.asset_class: row for row in rebalance_actions(holdings, target)}
    assert rows["equity"].action == "reduce" and rows["equity"].amount_change < 0
    assert sum(row.amount_change for row in rows.values()) == pytest.approx(0, abs=0.05)


def test_empty_portfolio_flags_nothing():
    assert all(row.action == "hold" for row in rebalance_actions({}, target_allocation("moderate", 10)))
```

**`tests/test_portfolio_stats.py`**

```python
# tests/test_portfolio_stats.py

import pytest

from app.services.investing.allocation import target_allocation
from app.services.investing.portfolio_stats import AssetAssumption, portfolio_return_and_volatility

ASSUMPTIONS = {
    "equity": AssetAssumption(0.11, 0.17), "debt": AssetAssumption(0.06, 0.04),
    "gold": AssetAssumption(0.07, 0.14), "cash": AssetAssumption(0.035, 0.005),
}


def test_all_equity_matches_the_equity_assumption():
    mean, vol = portfolio_return_and_volatility({"equity": 1.0}, ASSUMPTIONS)
    assert mean == pytest.approx(0.11) and vol == pytest.approx(0.17)


def test_diversification_lowers_volatility_below_the_weighted_average():
    weights = {"equity": 0.5, "debt": 0.5}
    _, vol = portfolio_return_and_volatility(weights, ASSUMPTIONS)
    assert vol < 0.5 * 0.17 + 0.5 * 0.04


def test_riskier_categories_have_higher_expected_return_and_volatility():
    stats = [portfolio_return_and_volatility(target_allocation(c, 15), ASSUMPTIONS)
             for c in ("conservative", "moderate", "aggressive")]
    assert [s[0] for s in stats] == sorted(s[0] for s in stats)
    assert [s[1] for s in stats] == sorted(s[1] for s in stats)


def test_missing_assumption_is_rejected():
    with pytest.raises(ValueError):
        portfolio_return_and_volatility({"equity": 1.0}, {})
```

**`tests/test_sip_planner.py`**

```python
# tests/test_sip_planner.py

import pytest

from app.services.investing.sip_planner import future_value_sip, recommend_sip, required_sip


def test_zero_return_future_value_is_just_the_deposits():
    assert future_value_sip(1000, 0.0, 5) == pytest.approx(60000)


def test_positive_return_beats_deposits_and_more_time_beats_less():
    assert future_value_sip(1000, 0.10, 5) > 60000
    assert future_value_sip(1000, 0.10, 10) > future_value_sip(1000, 0.10, 5)


def test_step_up_increases_the_corpus():
    assert future_value_sip(1000, 0.10, 10, annual_step_up=0.10) > future_value_sip(1000, 0.10, 10)


def test_required_sip_round_trips_with_future_value():
    sip = required_sip(1_000_000, 0.10, 8)
    assert future_value_sip(sip, 0.10, 8) == pytest.approx(1_000_000, rel=1e-6)


def test_recommendation_uses_safety_factor_and_reserves_emergency_topup():
    rec = recommend_sip(surplus_before_investing_low=20000, existing_sip=5000, emergency_gap=60000,
                        months_to_fill_emergency=12, safety_factor=0.8)
    assert rec.emergency_topup == 5000
    assert rec.capacity_total == pytest.approx(11000)          # 20000*0.8 - 5000
    assert rec.change_vs_existing == pytest.approx(6000)


def test_existing_sip_above_safe_level_gives_a_negative_change():
    rec = recommend_sip(surplus_before_investing_low=8000, existing_sip=10000, emergency_gap=0)
    assert rec.change_vs_existing < 0


def test_target_below_capacity_wins():
    rec = recommend_sip(30000, 0, 0, target_total=5000)
    assert rec.recommended_total == 5000 and rec.limited_by == "target"


def test_no_surplus_means_no_sip():
    assert recommend_sip(0, 0, 120000).recommended_total == 0


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        recommend_sip(1000, 0, 0, safety_factor=0)


def test_months_to_reach_matches_future_value():
    from app.services.investing.sip_planner import months_to_reach
    months = months_to_reach(500_000, 5000, 0.10)
    assert future_value_sip(5000, 0.10, months / 12) >= 500_000 > future_value_sip(5000, 0.10, (months - 1) / 12)


def test_bigger_sip_reaches_goal_sooner_and_zero_sip_never():
    from app.services.investing.sip_planner import months_to_reach
    assert months_to_reach(500_000, 7000, 0.10) < months_to_reach(500_000, 5000, 0.10)
    assert months_to_reach(500_000, 0, 0.10) is None
```

**`tests/test_monte_carlo.py`**

```python
# tests/test_monte_carlo.py

import pytest

from app.services.investing.monte_carlo import simulate_sip
from app.services.investing.sip_planner import future_value_sip


def test_same_seed_gives_identical_results():
    a = simulate_sip(5000, 5, 0.10, 0.15, n_paths=300, seed=7)
    b = simulate_sip(5000, 5, 0.10, 0.15, n_paths=300, seed=7)
    assert a == b


def test_percentiles_are_ordered():
    result = simulate_sip(5000, 5, 0.10, 0.18, n_paths=500)
    assert result.p10 <= result.p50 <= result.p90


def test_zero_volatility_matches_the_closed_form():
    result = simulate_sip(5000, 5, 0.10, 0.0, n_paths=10)
    assert result.p50 == pytest.approx(future_value_sip(5000, 0.10, 5), rel=1e-6)
    assert result.p10 == result.p90


def test_probability_falls_as_the_goal_rises():
    easy = simulate_sip(5000, 5, 0.10, 0.15, n_paths=500, goal=100_000).probability_of_goal
    hard = simulate_sip(5000, 5, 0.10, 0.15, n_paths=500, goal=1_000_000).probability_of_goal
    assert easy > hard


def test_no_goal_means_no_probability():
    assert simulate_sip(1000, 2, 0.08, 0.1, n_paths=50).probability_of_goal is None


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        simulate_sip(1000, 2, 0.08, -0.1)
```

**`tests/test_claims_guard.py`**

```python
# tests/test_claims_guard.py

import pytest

from app.services.investing.claims_guard import find_prohibited_claims, is_compliant


@pytest.mark.parametrize("text", [
    "This fund gives guaranteed returns.", "A risk-free way to grow money.", "You can't lose with an index fund.",
    "It will definitely beat inflation.", "Assured returns of 12%.", "A sure-shot plan.", "Zero risk investment.",
])
def test_promises_are_caught(text):
    assert not is_compliant(text)


def test_balanced_language_passes():
    assert is_compliant("Equity can fall sharply in the short term, so this plan keeps a debt cushion.")


def test_matches_are_reported():
    assert find_prohibited_claims("Guaranteed gains and zero risk.") == ["guaranteed", "zero risk"]


def test_even_the_negated_word_guarantee_is_flagged_on_purpose():
    # Known, deliberate over-strictness: negations are flagged too (fail safe -> template text is used).
    assert not is_compliant("The plan is not a guarantee of results; markets move.")
```


## 7. Code explanation (beginner level)

- **`risk_scoring.py`**: three questionnaire answers are averaged and rescaled to 0 to 100 (tolerance). Four finance facts (emergency months, income stability, dependents, horizon) are each turned into 0 to 100 and averaged (capacity). `build_risk_profile` converts both to a category and keeps the more cautious. `frozen=True` dataclasses mean results cannot be changed by accident.
- **`allocation.py`**: a dictionary of base splits per category. `equity_cap` looks up the maximum equity for the horizon. `target_allocation` moves the excess equity to debt and fixes rounding dust so the weights always sum to exactly 1. `rebalance_actions` compares what you hold with the target and returns rupee changes, flagging only big drifts.
- **`portfolio_stats.py`**: portfolio return is the weighted average. Portfolio volatility uses the standard formula with correlations, which is why mixing assets with low correlation lowers risk (diversification, shown in a test).
- **`sip_planner.py`**: `recommend_sip` = lower-band surplus x safety factor, minus the emergency top-up. `future_value_sip` loops month by month (compounding monthly, optional yearly step-up). `required_sip` inverts it. `months_to_reach` gives the "N months earlier" sentence.
- **`monte_carlo.py`**: runs 2,000 random return paths using a normal distribution and reports percentiles. A month cannot lose more than 99% (a guard against impossible values). Fixed `seed` gives repeatable results.
- **`claims_guard.py`**: a regular expression for promise words. `re.IGNORECASE` makes it case-insensitive; `\b` stops it matching inside other words.

## 8. Files still to build (contracts)

### `instrument_catalog.py` (static data, no returns, no names)

| Asset class | Instrument types to show | Plain-language note |
|---|---|---|
| equity | Broad-market index fund or ETF; flexi-cap fund | Higher long-term growth, can fall sharply in the short term |
| debt | Short-duration debt fund; government securities fund; fixed deposit | Lower swings, returns can still vary |
| gold | Gold ETF; sovereign-gold-type instrument if available | Diversifier, price can be flat for years |
| cash | Savings account; liquid fund | Safety and access, loses to inflation over time |

### `assumptions.py` (reads from `Settings`, all overridable by environment variables)

Defaults are ILLUSTRATIVE PLACEHOLDERS for development, to be replaced with cited figures before the paper:

```text
INVEST_EQUITY_RETURN=0.11   INVEST_EQUITY_VOL=0.17
INVEST_DEBT_RETURN=0.06     INVEST_DEBT_VOL=0.04
INVEST_GOLD_RETURN=0.07     INVEST_GOLD_VOL=0.14
INVEST_CASH_RETURN=0.035    INVEST_CASH_VOL=0.005
INVEST_SIP_SAFETY_FACTOR=0.8
INVEST_MC_PATHS=2000        INVEST_MC_SEED=42
```

### `investment_service.py` (orchestration, no maths of its own)
1. Load profile and holdings for `user_id`. If no profile, return a clear 409 "complete the risk profile first".
2. Emergency months = liquid savings / average monthly essential spend (from the active Phase 4 budget; fall back to the last 3 months of essentials from transactions).
3. Surplus before investing, lower band: from the latest Phase 5 forecast run. If no forecast exists, say so and fall back to income minus budgeted spending, with a warning `"forecast_missing"`. Never silently guess.
4. `build_risk_profile`, `target_allocation`, `rebalance_actions`, `recommend_sip`, `portfolio_return_and_volatility`, `simulate_sip` (current SIP and recommended SIP), `months_to_reach` for both.
5. Collect everything into one dict `facts` (aggregates only), call the explainer, store the plan.

### `investment_explainer.py`
- One LCEL call: input is `facts` only (percentages, categories, SIP amounts, ranges). No transaction text, no names.
- Output must pass `explanation_guard` (every rupee amount must exist in `facts`) AND `claims_guard.is_compliant`.
- On any failure (provider error, guard fail), build the deterministic template and set `explanation_source="template"`.
- The text must mention: the limiting factor, that projections are scenarios under stated assumptions, and the disclaimer is added by code, not by the LLM.

## 9. Research plan (IEEE angle)

**Contribution statement (draft):** an explainable, budget-aware investment planning pipeline in which deterministic, auditable financial logic is separated from LLM narration, with capacity-gated risk profiling, forecast-uncertainty-aware SIP sizing, and a compliance guard on generated text.

### Evaluation set
Synthetic personas (about 500), generated with a fixed seed across: age bands, income, dependents, emergency months 0 to 12, income stability 1 to 5, horizon 1 to 25 years, questionnaire answers. Be honest in the paper: there is no ground truth for a "suitable" allocation, so we define **suitability rules** (for example "no equity above 20% for a horizon under 3 years", "no investing with under 3 months of emergency fund") written by the team BEFORE running the methods, and measure violations. State this limitation.

### Baselines
- B0: fixed 60/40 equity/debt for everyone.
- B1: age-based rule ("100 minus age" in equity).
- B2: questionnaire only (tolerance score, no capacity, no horizon cap).
- Proposed P: tolerance and capacity (safer wins) plus horizon glide plus emergency-first SIP.

### Metrics
1. **Suitability violation rate** against the pre-registered rules (lower is better).
2. **Shortfall risk**: probability the p10 outcome is below the amount invested (capital loss) at the horizon.
3. **Goal probability** at the recommended SIP.
4. **SIP affordability failure rate**: replay Phase 5 synthetic history and count months where the recommended SIP exceeds the actual surplus, compared with an average-surplus sizing rule. This links Phase 5 uncertainty to Phase 6 outcomes.
5. **Explanation quality**: numeric faithfulness rate, prohibited-claims rate of RAW LLM output (before the guard), template fallback rate, LLM calls per plan (target: 1).
6. **Sensitivity**: results under low / base / high return assumptions.

### Ablations
Remove capacity (tolerance only), remove horizon glide, remove emergency-first reservation, safety factor in {1.0, 0.8, 0.6}, lower band vs mean surplus, guard on vs off.

### Datasets
Synthetic personas (own). For return assumptions cite published long-run index and bond statistics and verify them before quoting. Real index history arrives in Phase 7 and can then replace the normal-return assumption with historical bootstrap, a strong follow-up experiment (Monte Carlo normal vs historical bootstrap).

### Known limitations to state
Normal returns understate fat tails; normal assumptions are illustrative until cited; synthetic personas; no tax, fees or inflation modelling in Phase 6 (inflation can be an optional real-return toggle); the guard is strict and has false positives on negations.

## 10. Implementation steps for Claude Code (gated prompts)

Stop after each prompt and report. Use Plan mode. Do not start the next until the previous passes.

### Prompt 6A-0: audit and branch
```text
Read CLAUDE.md and docs/phase6-spec.md. Check git: confirm Phase 5 is merged into DEV (git fetch origin; git branch -r --merged origin/DEV). Cut feature/vishesh-investing from the updated DEV. Then audit and report (no code yet): (1) where the latest forecast run stores its monthly surplus and interval bands, and the exact field names; (2) how to get average monthly essential spending from the active budget; (3) the name of the existing numeric explanation guard and how the budget explainer calls it; (4) current test count. Tell me anything in docs/phase6-spec.md that does not fit the code. Wait for my go-ahead.
```

### Prompt 6A-1: pure modules
```text
Create backend/app/services/investing/ with the six modules from section 6 of docs/phase6-spec.md and backend/tests/ with the six test files, copied EXACTLY. Add the empty __init__.py. Run: docker compose exec backend python -m pytest tests -q. Expect 65 new tests to pass on top of the existing ones. Do not change any logic. Report the totals.
```

### Prompt 6A-2: data model
```text
Add models/investment.py (InvestmentProfile, Holding, InvestmentPlan) and schemas/investment.py exactly as in section 4 (tables). Create Alembic migration 0005_investing.py (head must follow 0004). Money columns Numeric(14,2). Add a unique constraint on (user_id) for profiles and (user_id, asset_class, label) for holdings. Run alembic upgrade head inside the container, and add a test that the migration applies on a clean database. Report.
```

### Prompt 6A-3: settings, assumptions, catalog, service
```text
Add the INVEST_* settings (section 8) to core/config.py and backend/.env.example with the illustrative defaults and a comment that they are placeholders to be cited before publication. Create assumptions.py, instrument_catalog.py and services/investment_service.py following section 8. The service must: filter every query by user_id, return a 409-style domain error when the profile is missing, add the warning "forecast_missing" when no forecast exists, never raise on missing holdings (treat as empty). Write service tests with a seeded user and a stubbed forecast covering: capacity-limited user, tolerance-limited user, no emergency fund (SIP reserves top-up), short horizon cap, missing forecast warning, user isolation. Report.
```

### Prompt 6A-4: explainer agent
```text
Create agents/investment_explainer.py. ONE LLM call through the existing provider abstraction (no vendor SDK imports). Input is only the aggregate facts dict. Accept the text only if the existing numeric guard passes AND claims_guard.is_compliant passes. Otherwise return a deterministic template and set explanation_source="template". The disclaimer is appended by code. Tests with a stub LLM: valid text accepted; invented rupee amount rejected; "guaranteed returns" rejected; provider exception gives template; call count is exactly 1. Report.
```

### Prompt 6A-5: routes
```text
Create api/routes/investing.py with the endpoints in section 4 (API design), register the router, JWT on every route, user_id from the token only. Every response includes a "disclaimer" field. POST /simulate is not stored and has bounds (years 1 to 40, SIP up to a sane maximum, n_paths fixed by settings). Add route tests: auth required, validation errors, user isolation, disclaimer present. Extend scripts/smoke_test.py with one step (profile, holdings, plan, simulate). Run the full test suite and the smoke test, then commit with clear messages. Report totals.
```

### Prompt 6B: evaluation and research docs
```text
Create backend/evaluation/investing/ with: a seeded persona generator (about 500 personas, fixed seed), the four methods B0, B1, B2 and Proposed, the pre-registered suitability rules written in a rules.py BEFORE the methods run, and a runner that writes results to docs/investing-evaluation.md with the metrics, ablations and sensitivity table from section 9 of the spec. Use only the Python standard library and the approved stack. Be honest in the write-up: label results as synthetic and list the limitations. Then write docs/investing.md (purpose, architecture, flow diagram, input, output, API, tables, testing) and update CLAUDE.md status. Report the key numbers.
```

### Prompt 6C: frontend
```text
Add frontend/app/investing/page.tsx in the existing console style (Tailwind, Shadcn UI, Recharts only). Sections: (1) risk questionnaire and facts form that shows the resulting profile with the limiting factor; (2) holdings editor; (3) Generate plan button; (4) results: target vs current allocation (bar chart), rebalance table, SIP card (capacity, recommendation, emergency top-up), projection chart with p10/p50/p90 range and probability of goal, instrument types, assumptions panel, warnings, explanation with an "AI-written / template" badge; (5) what-if simulator calling POST /simulate. A permanent disclaimer banner: "Educational planning tool, not financial advice. Projections are scenarios under stated assumptions, not guarantees." Add the page to the navigation. Run npm run build and fix errors. Report.
```

## 11. Common beginner mistakes
- Putting maths inside the LLM prompt. The model will round, invent and contradict itself. Python computes, the model narrates.
- Using the average surplus for the SIP. One bad month then breaks the plan.
- Recommending equity to someone with no emergency fund because their questionnaire said "brave".
- Writing `Float` for money columns. Use `Numeric(14,2)`.
- Forgetting `user_id` filters. One user would see another's holdings.
- Presenting a projection as a prediction. Always show the range and the assumptions.
- Hard-coding return assumptions inside functions instead of settings.
- Letting the simulation be random each run (no seed), so tests and paper results cannot be reproduced.

## 12. Testing steps (after everything is built)
1. `docker compose exec backend python -m pytest tests -q` (all green).
2. `docker compose exec backend python -m scripts.smoke_test` (investing step passes).
3. `docker compose exec backend python -m evaluation.investing.run` and read `docs/investing-evaluation.md`.
4. In the app: open the Investing page, save a profile with an emergency fund under 3 months and a brave questionnaire. The category must come out conservative with limiting factor "capacity". Then raise the emergency fund above 6 months and watch the category rise.

## 13. Next steps
Phase 7 (Stock Intelligence Agent) adds market data, FinBERT news sentiment and PyTorch models, and plugs into this phase as an optional satellite slice and as real historical returns for a bootstrap Monte Carlo. Phase 8 (Goal Planning) reuses `required_sip`, `months_to_reach` and `simulate_sip`, which is why they were written as pure, reusable functions.

## 14. Audit decisions (post 6A-0)

Agreed after the 6A-0 audit of the Phase 5 code (541 tests passing on `feature/vishesh-investing`, cut from DEV at `f802e20`). Where these decisions differ from an earlier section, **these win**.

### 14.1 Forecast surplus (replaces "lower band surplus" in sections 4 and 8)
- `forecast_runs` stores horizon TOTALS, not monthly figures, and `net_cashflow` has no band and is AFTER existing investments. So the service derives the number itself.
- Use the user's latest **30-day** run (`horizon_days == 30`): a 90-day sum interval divided by 3 is narrower per month than a one-month interval, and the SIP must be affordable every month.
- `low_surplus_monthly = (expected_income - expected_fixed_expenses - expected_variable_expenses_upper) * 30.44 / horizon_days`
  (equivalent to `projected_end_balance_lower - opening_balance + expected_investments`, scaled).
- The run id used goes into `inputs_json` (`forecast_run_id`), so a plan can be traced to its forecast.
- Warnings: `forecast_missing` (no 30-day run: fall back to income minus budgeted spending, as in section 8) and `forecast_stale` (the run's `as_of` is too old; threshold is a setting).
- Limitation to state: income has no band, so for irregular earners the lower surplus is optimistic. The 0.8 safety factor partly covers this.
- `forecast_service` gets a small public helper that returns the latest run row (optionally of one horizon) or `None`, because `latest_run()` raises 404 and returns a float dict.

### 14.2 Essential spending (replaces "active Phase 4 budget" in sections 4 and 8)
- There is no "active budget": `budgets` holds one row per (user, month, category).
- New public `budget_service.average_essential_spending(db, user_id, month)`:
  if that month has budgets, sum `limit_amount` of the essential categories (`budget_calculator.is_essential`), so user overrides count;
  otherwise the essential categories of the transaction history average over `BUDGET_LOOKBACK_MONTHS` (3).
  It reports which source was used and how many months.

### 14.3 Explanation guards (extends section 8, `investment_explainer.py`)
- `facts` carries PRE-FORMATTED display strings for every number, and the prompt tells the model to copy them exactly.
- The rupee format is chosen so `explanation_guard.is_faithful` parses it correctly, including Indian digit grouping (`₹12,34,567`); this is covered by a test.
- On top of `is_faithful` and `claims_guard.is_compliant`, the explainer rejects:
  any percentage not in `facts` (1 percentage point tolerance), and
  any text containing `lakh`, `crore`, or `k` / `L` amount suffixes (they would be mis-parsed by the rupee guard).
- Any rejection -> deterministic template.

### 14.4 Research logging (replaces `explanation_source` alone in section 4)
- `investment_plans` stores `llm_used`, `explanation_faithful`, `claims_compliant`, `llm_calls` and `raw_prohibited_claims` (count found in the RAW LLM text, before the guard).
- `explanation_source` (`llm` | `template`) is DERIVED from these, not stored separately.
- "One LLM call" means one call per provider attempt: the provider fallback chain (as in Phases 4 and 5) can make more after a provider error. The 6A-4 "exactly 1 call" test uses a single stub.

### 14.5 Smaller fixes (items 5-9 of the audit)
- `holdings.label` is NOT NULL with default `''`: NULLs are distinct in a UNIQUE constraint on both Postgres and SQLite, so (user_id, asset_class, NULL) would allow duplicates.
- "409-style domain error" = `HTTPException(409)` raised in the service, the existing codebase convention.
- Pure modules use float; the service converts to `Decimal` (`core/money.py`) before saving or returning money.
- The `capacity_score` saved by PUT /profile can go stale as essential spending changes; POST /plan always recomputes it.
- The forecast's `expected_investments` (detected SIPs) can pre-fill or cross-check the user-entered existing SIP.
- Phase 5 interval coverage is 61-65% (not 63%) against 80% nominal.

### 14.6 Conventions (replaces paths in sections 3, 10 and 12)
- Docs: `docs/modules/investing.md` and `docs/modules/investing-evaluation.md`.
- Evaluation: flat `backend/evaluation/evaluate_investing.py` writing `backend/evaluation/investing_metrics.csv` and `.md`
  (run with `python -m evaluation.evaluate_investing`), like `evaluate_budget.py` and `evaluate_forecast.py`.
- Test files keep the reference names from section 6 (`test_risk_scoring.py` and so on); later files may use `test_investing_*.py`.

### 14.7 Change to the reference code (6A-1)
- `claims_guard.py`: `can'?t lose` became `can[’']?t lose`, so the curly apostrophe that LLMs often produce is caught too.
  New test `test_curly_straight_and_missing_apostrophes_are_all_caught` (3 cases). 6A-1 total: 68 new tests.

### 14.8 Changes after the 6B evaluation (one round only)
Rule for the whole phase: ONE round of fixes found by the evaluation, each logged with before/after numbers in
`docs/modules/investing-evaluation.md` ("Changes after seeing results"). No second round of tuning against the rules.
- **Fix 1 (rule R4):** `recommend_sip` caps the emergency top-up at the low-band surplus. When the surplus is smaller,
  the SIP is 0, all of it goes to the fund, `emergency_topup_capped` is true and the plan warns `emergency_fill_slow`
  (filling takes longer than 12 months). R4 becomes 0% by design: a regression test, not evidence.
- **Fix 2 (rule R5):** `CAPACITY_CAP_FRAGILE_HOUSEHOLD = 34` (conservative) when income stability <= 2 and
  dependents >= 2, the same pattern as the emergency-fund cap. R5 is relabelled `fixed_after_evaluation`.
- Engine version 6.0 -> 6.1 (stored plans record it).
- **Claims guard v2:** surely, for sure, locked in, fully protected, completely safe, no downside, cannot/can't fail,
  no chance of losing, never at a loss. Chosen after seeing v1's misses, so v1 numbers stay labelled "v1, on the
  original 60 sentences". Decision (2026-10-23): no teammate-written rules or sentences; the write-up states
  that all rules and guard sentences were written by the same author as the method (no independent labelling),
  so v2 recall is not measured on independent data; the real-LLM run is the only outside check.
- Reporting only (no method change): R7 "not evaluated (n=1)" in the main run plus a separate stratified run; goal
  gap; variability sensitivity with the primary 5-30% range unchanged.
