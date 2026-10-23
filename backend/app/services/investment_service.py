# app/services/investment_service.py
#
# Purpose: business logic of the Investment Advisor Agent (Phase 6). Orchestration
# only: it reads the database, the latest Phase 5 forecast and the Phase 4 budget,
# and hands plain numbers to the pure modules in services/investing/, which do ALL
# the maths. It has no formulas of its own beyond unit conversion.
#
# generate_plan() for one user and one "today" (as_of):
#   1. profile (409 if missing) and holdings (missing = empty, never an error)
#   2. emergency months = liquid savings / average monthly essential spending
#   3. risk profile: tolerance vs capacity, the safer one wins
#   4. target allocation for the horizon (goal years, else the profile horizon), drift
#   5. lower-band monthly surplus BEFORE investing from the latest 30-day forecast
#      (fallback: income minus budgets, with "forecast_missing"; never a silent guess)
#   6. SIP: emergency top-up first, safety factor, capped by the goal's required SIP
#   7. seeded Monte Carlo at the existing and the recommended SIP
#   8. explanation: template or ONE guarded LLM call (agents/investment_explainer.py)
#   9. store an investment_plans row with the explanation's research log
#
# Security: every query filters by the user_id from the JWT.

import logging
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.agents.investment_explainer import InvestmentExplainer
from app.core.config import settings
from app.core.money import to_money
from app.models.forecast import ForecastRun
from app.models.investment import Holding, InvestmentPlan, InvestmentProfile
from app.schemas.investment import HoldingIn, InvestmentProfileUpdate, SimulateRequest
from app.services import budget_service, forecast_service
from app.services.budget_service import ESSENTIALS_NONE, EssentialSpending
from app.services.investing.allocation import ASSET_CLASSES, rebalance_actions, target_allocation
from app.services.investing.assumptions import InvestingAssumptions, load_assumptions
from app.services.investing.claims_guard import find_prohibited_claims
from app.services.investing.instrument_catalog import instruments_for
from app.services.investing.monte_carlo import simulate_sip
from app.services.investing.portfolio_stats import portfolio_return_and_volatility
from app.services.investing.risk_scoring import CapacityFacts, RiskProfile, ToleranceAnswers, build_risk_profile
from app.services.investing.sip_planner import months_to_reach, recommend_sip, required_sip

logger = logging.getLogger(__name__)

# Bump when the maths changes, so stored plans stay comparable.
# 6.1: emergency top-up capped at the surplus; fragile-household capacity cap (both found by the 6B evaluation)
ENGINE_VERSION = "6.1"
FORECAST_HORIZON_DAYS = 30        # one month: the SIP must be affordable EVERY month (spec 14.1)
DAYS_PER_MONTH = Decimal("30.44")

# Warning codes (shown to the user and stored with the plan)
WARNING_FORECAST_MISSING = "forecast_missing"
WARNING_FORECAST_STALE = "forecast_stale"
WARNING_SURPLUS_UNKNOWN = "surplus_unknown"
WARNING_ESSENTIALS_UNKNOWN = "essentials_unknown"
WARNING_NO_HOLDINGS = "no_holdings"
WARNING_EMERGENCY_FILL_SLOW = "emergency_fill_slow"   # the surplus is smaller than the top-up the fill period needs

SURPLUS_FROM_FORECAST = "forecast"
SURPLUS_FROM_BUDGET = "budget"
SURPLUS_NONE = "none"

_ZERO = Decimal("0.00")


def _month_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _emergency_months(liquid_savings: Decimal, essentials: EssentialSpending) -> float:
    """Months of essential spending the savings cover. Unknown essentials count as 0 (fail safe)."""
    if essentials.amount <= 0:
        return 0.0
    return round(float(liquid_savings / essentials.amount), 2)


def _risk_dict(risk: RiskProfile) -> dict:
    return asdict(risk)


def _build_explainer() -> InvestmentExplainer:
    """Replaced by a fake in the tests (monkeypatch), like forecast_service._build_explainer."""
    return InvestmentExplainer.from_settings(find_prohibited_claims)


# --- Profile ---

def _get_profile(db: Session, user_id: int) -> InvestmentProfile | None:
    return db.query(InvestmentProfile).filter(InvestmentProfile.user_id == user_id).first()


def _stored_risk(profile: InvestmentProfile) -> RiskProfile:
    """Rebuilds the risk profile from the stored inputs (cheap, and the categories match exactly)."""
    facts = profile.capacity_facts
    return build_risk_profile(
        ToleranceAnswers(**profile.tolerance_answers),
        CapacityFacts(facts["emergency_fund_months"], facts["income_stability"], facts["dependents"],
                      facts["horizon_years"]),
    )


def profile_to_response(profile: InvestmentProfile) -> dict:
    facts = profile.capacity_facts
    return {
        "answers": profile.tolerance_answers,
        "liquid_savings": float(profile.liquid_savings),
        "existing_monthly_sip": float(profile.existing_monthly_sip),
        "income_stability": facts["income_stability"],
        "dependents": facts["dependents"],
        "horizon_years": facts["horizon_years"],
        "emergency_fund_months": facts["emergency_fund_months"],
        "risk_profile": _risk_dict(_stored_risk(profile)),
        "updated_at": profile.updated_at,
    }


def upsert_profile(db: Session, user_id: int, data: InvestmentProfileUpdate, as_of: date) -> dict:
    """Saves the questionnaire and facts (one row per user) and returns the computed risk profile."""
    essentials = budget_service.average_essential_spending(db, user_id, _month_of(as_of))
    months = _emergency_months(data.liquid_savings, essentials)
    answers = data.answers.model_dump()
    risk = build_risk_profile(
        ToleranceAnswers(**answers), CapacityFacts(months, data.income_stability, data.dependents, data.horizon_years)
    )

    profile = _get_profile(db, user_id)
    if profile is None:
        profile = InvestmentProfile(user_id=user_id)
        db.add(profile)
    profile.tolerance_answers = answers
    profile.capacity_facts = {
        "income_stability": data.income_stability, "dependents": data.dependents,
        "horizon_years": data.horizon_years, "emergency_fund_months": months,
    }
    profile.liquid_savings = to_money(data.liquid_savings)
    profile.existing_monthly_sip = to_money(data.existing_monthly_sip)
    profile.tolerance_score = Decimal(str(risk.tolerance_score))
    profile.capacity_score = Decimal(str(risk.capacity_score))
    profile.category = risk.category
    profile.limiting_factor = risk.limiting_factor
    db.commit()
    db.refresh(profile)
    logger.info(
        f"Investment profile saved user_id={user_id} category={risk.category} limiting={risk.limiting_factor} "
        f"emergency_months={months} essentials_source={essentials.source}"
    )
    return profile_to_response(profile)


def get_profile(db: Session, user_id: int) -> dict:
    profile = _get_profile(db, user_id)
    if profile is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No risk profile yet. Create one with PUT /api/investing/profile.")
    return profile_to_response(profile)


# --- Holdings ---

def _holding_rows(db: Session, user_id: int) -> list[Holding]:
    return db.query(Holding).filter(Holding.user_id == user_id).order_by(Holding.asset_class, Holding.label).all()


def _amounts_by_class(rows: list[Holding]) -> dict[str, float]:
    totals = {name: _ZERO for name in ASSET_CLASSES}
    for row in rows:
        totals[row.asset_class] += row.amount
    return {name: float(amount) for name, amount in totals.items()}


def get_holdings(db: Session, user_id: int) -> dict:
    """Holdings with each row's share of the total and the share per asset class."""
    rows = _holding_rows(db, user_id)
    total = sum((row.amount for row in rows), _ZERO)
    by_class = _amounts_by_class(rows)
    return {
        "holdings": [
            {"asset_class": row.asset_class, "amount": float(row.amount), "label": row.label, "as_of": row.as_of,
             "weight": round(float(row.amount / total), 4) if total > 0 else 0.0}
            for row in rows
        ],
        "total": float(total),
        "weights_by_class": {
            name: round(amount / float(total), 4) if total > 0 else 0.0 for name, amount in by_class.items()
        },
    }


def replace_holdings(db: Session, user_id: int, items: list[HoldingIn], as_of: date) -> dict:
    """Replaces the user's whole holdings list in one transaction (an empty list clears it)."""
    db.query(Holding).filter(Holding.user_id == user_id).delete(synchronize_session=False)
    for item in items:
        db.add(Holding(
            user_id=user_id, asset_class=item.asset_class, amount=to_money(item.amount), label=item.label,
            as_of=item.as_of or as_of,
        ))
    db.commit()
    logger.info(f"Holdings replaced user_id={user_id} rows={len(items)}")
    return get_holdings(db, user_id)


# --- Surplus (Phase 5 forecast, or the Phase 4 budget as a fallback) ---

@dataclass(frozen=True)
class SurplusEstimate:
    monthly_low: Decimal                     # lower-band monthly surplus BEFORE investing
    source: str                              # SURPLUS_FROM_FORECAST | SURPLUS_FROM_BUDGET | SURPLUS_NONE
    forecast_run_id: int | None = None
    forecast_as_of: date | None = None
    detected_monthly_sip: Decimal | None = None   # SIPs the forecast found in the statements


def low_surplus_from_run(run: ForecastRun) -> Decimal:
    """
    Lower-band monthly surplus before investing (spec 14.1): the worst case is the
    HIGHEST variable spending, so the upper variable band is used. Income has no band
    (Phase 5 treats it as known). Horizon totals are scaled to one month.
    """
    total = run.expected_income - run.expected_fixed_expenses - run.expected_variable_expenses_upper
    return to_money(total * DAYS_PER_MONTH / Decimal(run.horizon_days))


def _estimate_surplus(db: Session, user_id: int, as_of: date, warnings: list[str]) -> SurplusEstimate:
    run = forecast_service.latest_run_row(db, user_id, FORECAST_HORIZON_DAYS)
    if run is not None:
        if (as_of - run.as_of).days > settings.INVEST_FORECAST_MAX_AGE_DAYS:
            warnings.append(WARNING_FORECAST_STALE)
        detected = to_money(run.expected_investments * DAYS_PER_MONTH / Decimal(run.horizon_days))
        return SurplusEstimate(low_surplus_from_run(run), SURPLUS_FROM_FORECAST, run.id, run.as_of, detected)

    warnings.append(WARNING_FORECAST_MISSING)
    financial = budget_service.get_profile(db, user_id)
    budgets = budget_service.list_budgets(db, user_id, _month_of(as_of))
    if financial is not None and budgets:
        budgeted = sum((b.limit_amount for b in budgets), _ZERO)
        return SurplusEstimate(to_money(financial.monthly_income - budgeted), SURPLUS_FROM_BUDGET)

    warnings.append(WARNING_SURPLUS_UNKNOWN)          # nothing to size a SIP from: recommend 0
    return SurplusEstimate(_ZERO, SURPLUS_NONE)


# --- Projections ---

def _projection(
    monthly_sip: float, years: float, mean: float, volatility: float, goal: float | None,
    assumptions: InvestingAssumptions, annual_step_up: float = 0.0,
) -> dict:
    result = simulate_sip(
        monthly_sip, years, mean, volatility, n_paths=assumptions.mc_paths, seed=assumptions.mc_seed,
        goal=goal, annual_step_up=annual_step_up,
    )
    # months_to_reach has no step-up, so it is only reported for a flat SIP
    months = months_to_reach(goal, monthly_sip, mean) if goal is not None and annual_step_up == 0 else None
    return {
        "monthly_sip": round(monthly_sip, 2), "years": years, "p10": result.p10, "p50": result.p50,
        "p90": result.p90, "probability_of_goal": result.probability_of_goal, "months_to_goal": months,
        "n_paths": result.n_paths,
    }


# --- Plans ---

def generate_plan(
    db: Session, user_id: int, as_of: date, goal_amount: Decimal | None = None, goal_years: float | None = None
) -> dict:
    """Builds and stores one plan; returns it in the InvestmentPlanResponse shape."""
    # 1. Inputs
    profile = _get_profile(db, user_id)
    if profile is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Complete the risk profile first (PUT /api/investing/profile)."
        )
    warnings: list[str] = []
    rows = _holding_rows(db, user_id)
    if not rows:
        warnings.append(WARNING_NO_HOLDINGS)
    facts = profile.capacity_facts

    # 2. Emergency fund, recomputed from today's essential spending
    essentials = budget_service.average_essential_spending(db, user_id, _month_of(as_of))
    if essentials.source == ESSENTIALS_NONE:
        warnings.append(WARNING_ESSENTIALS_UNKNOWN)
    emergency_months = _emergency_months(profile.liquid_savings, essentials)
    target_months = Decimal(str(settings.INVEST_EMERGENCY_TARGET_MONTHS))
    emergency_gap = max(_ZERO, to_money(essentials.amount * target_months - profile.liquid_savings))

    # 3. Risk profile
    risk = build_risk_profile(
        ToleranceAnswers(**profile.tolerance_answers),
        CapacityFacts(emergency_months, facts["income_stability"], facts["dependents"], facts["horizon_years"]),
    )

    # 4. Allocation for the horizon the money is needed in, and drift from the holdings
    years = float(goal_years) if goal_years is not None else float(facts["horizon_years"])
    target = target_allocation(risk.category, years)
    rebalance = rebalance_actions(_amounts_by_class(rows), target)
    assumptions = load_assumptions()
    mean, volatility = portfolio_return_and_volatility(target, assumptions.assets)

    # 5-6. Surplus and SIP
    surplus = _estimate_surplus(db, user_id, as_of, warnings)
    goal = float(goal_amount) if goal_amount is not None else None
    needed = required_sip(goal, mean, years) if goal is not None and round(years * 12) >= 1 else None
    sip = recommend_sip(
        float(surplus.monthly_low), float(profile.existing_monthly_sip), float(emergency_gap),
        months_to_fill_emergency=settings.INVEST_EMERGENCY_FILL_MONTHS,
        safety_factor=assumptions.sip_safety_factor, target_total=needed,
    )
    if sip.emergency_topup_capped:
        warnings.append(WARNING_EMERGENCY_FILL_SLOW)

    # 7. Ranges at the current and the recommended SIP
    current = _projection(float(profile.existing_monthly_sip), years, mean, volatility, goal, assumptions)
    recommended = _projection(sip.recommended_total, years, mean, volatility, goal, assumptions)

    # 8. Store
    result = {
        "risk_profile": _risk_dict(risk),
        "target_allocation": target,
        "current_allocation": {row.asset_class: row.current_weight for row in rebalance},
        "rebalance": [asdict(row) for row in rebalance],
        "sip": asdict(sip),
        "projection_current": current,
        "projection_recommended": recommended,
        "portfolio": {"expected_return": round(mean, 6), "volatility": round(volatility, 6)},
        "emergency_fund": {
            "essential_monthly_spend": float(essentials.amount), "essentials_source": essentials.source,
            "months_covered": emergency_months, "target_months": float(target_months), "gap": float(emergency_gap),
        },
        "surplus": {
            "monthly_low": float(surplus.monthly_low), "source": surplus.source,
            "forecast_as_of": surplus.forecast_as_of.isoformat() if surplus.forecast_as_of else None,
        },
        "goal": {"amount": goal, "years": years, "required_sip": round(needed, 2) if needed is not None else None},
        "instruments": instruments_for(target),
        "warnings": warnings,
    }
    inputs = {
        "as_of": as_of.isoformat(),
        "goal_amount": goal, "goal_years": goal_years,
        "profile": {
            "answers": profile.tolerance_answers, "capacity_facts": facts,
            "liquid_savings": float(profile.liquid_savings), "existing_monthly_sip": float(profile.existing_monthly_sip),
        },
        "holdings": [{"asset_class": r.asset_class, "amount": float(r.amount), "label": r.label} for r in rows],
        "forecast_run_id": surplus.forecast_run_id,
        "surplus_source": surplus.source,
        "detected_monthly_sip": float(surplus.detected_monthly_sip) if surplus.detected_monthly_sip is not None else None,
        "essentials_source": essentials.source,
        "essentials_months_used": essentials.months_used,
    }
    assumptions_dict = assumptions.to_dict()
    explanation = _build_explainer().explain(result, assumptions_dict)
    plan = InvestmentPlan(
        user_id=user_id, inputs_json=inputs, result_json=result, assumptions_json=assumptions_dict,
        engine_version=ENGINE_VERSION, explanation=explanation.text, llm_used=explanation.llm_used,
        explanation_faithful=explanation.explanation_faithful, claims_compliant=explanation.claims_compliant,
        llm_calls=explanation.llm_calls, raw_prohibited_claims=explanation.raw_prohibited_claims,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    logger.info(
        f"Investment plan generated plan={plan.id} user_id={user_id} category={risk.category} "
        f"limiting={risk.limiting_factor} surplus_source={surplus.source} sip={sip.recommended_total} "
        f"limited_by={sip.limited_by} warnings={warnings} llm_used={explanation.llm_used} "
        f"llm_calls={explanation.llm_calls} rejected_for={explanation.rejected_for}"
    )
    return plan_to_response(plan)


def plan_to_response(plan: InvestmentPlan) -> dict:
    """A stored plan in the InvestmentPlanResponse shape."""
    return {
        **plan.result_json,
        "id": plan.id,
        "created_at": plan.created_at,
        "assumptions": plan.assumptions_json,
        "forecast_run_id": plan.inputs_json.get("forecast_run_id"),
        "explanation": plan.explanation,
        "explanation_source": plan.explanation_source,
        "llm_used": plan.llm_used,
        "explanation_faithful": plan.explanation_faithful,
        "claims_compliant": plan.claims_compliant,
        "engine_version": plan.engine_version,
    }


def _plans_newest_first(db: Session, user_id: int):
    # Matches the (user_id, created_at DESC) index; id breaks ties within the same second
    return (
        db.query(InvestmentPlan).filter(InvestmentPlan.user_id == user_id)
        .order_by(InvestmentPlan.created_at.desc(), InvestmentPlan.id.desc())
    )


def latest_plan(db: Session, user_id: int) -> dict:
    plan = _plans_newest_first(db, user_id).first()
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No plan yet. Generate one with POST /api/investing/plan.")
    return plan_to_response(plan)


def get_plan(db: Session, user_id: int, plan_id: int) -> dict:
    """One of the user's plans. Someone else's id is a 404, so we never reveal that it exists."""
    plan = (
        db.query(InvestmentPlan)
        .filter(InvestmentPlan.id == plan_id, InvestmentPlan.user_id == user_id)
        .first()
    )
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found.")
    return plan_to_response(plan)


def list_plans(db: Session, user_id: int, limit: int) -> dict:
    plans = _plans_newest_first(db, user_id).limit(limit).all()
    return {"plans": [
        {"id": plan.id, "created_at": plan.created_at, "category": plan.result_json["risk_profile"]["category"],
         "recommended_sip": plan.result_json["sip"]["recommended_total"],
         "explanation_source": plan.explanation_source}
        for plan in plans
    ]}


# --- What-if and assumptions (nothing stored) ---

def simulate(data: SimulateRequest) -> dict:
    """POST /simulate: any SIP, years and category under the current assumptions. Not stored."""
    assumptions = load_assumptions()
    target = target_allocation(data.category, data.years)
    mean, volatility = portfolio_return_and_volatility(target, assumptions.assets)
    goal = float(data.goal_amount) if data.goal_amount is not None else None
    return {
        "category": data.category, "target_allocation": target,
        "expected_return": round(mean, 6), "volatility": round(volatility, 6),
        "projection": _projection(float(data.monthly_sip), data.years, mean, volatility, goal, assumptions,
                                  annual_step_up=data.annual_step_up),
    }


def get_assumptions() -> dict:
    return load_assumptions().to_dict()
