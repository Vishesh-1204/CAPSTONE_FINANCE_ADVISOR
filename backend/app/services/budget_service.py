# app/services/budget_service.py
#
# Purpose: business logic of the Budget Planning Agent (Phase 4).
#   * financial profile (income + savings target)
#   * generate a monthly plan: read history -> calculator -> planner agent ->
#     save limits (keeping the user's overrides) -> log a budget_runs row
#   * override / reset one limit
#   * status: actual spending of a month vs its limits
#
# Spending rule (same as summary_service): only EXPENSES count. A row counts as
# an expense when txn_type='expense', or when txn_type is still NULL (a pre-v3
# row) and it is a debit. Investments, redemptions, income and self-transfers
# never count as spending.
#
# Security: every query filters by the user_id from the JWT; someone else's
# budget id is a 404 (we never reveal that it exists).

import logging
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.agents.budget_planner import BudgetFact, BudgetPlannerAgent, PlanContext
from app.core.categories import CATEGORY_NAMES, TRANSFERS_CATEGORY
from app.core.config import settings
from app.core.money import to_money
from app.core.month_utils import lookback_window, month_bounds, month_key
from app.core.transaction_types import DEBIT, TXN_EXPENSE, TXN_INVESTMENT_OUT
from app.models.budget import SOURCE_AGENT, SOURCE_USER, Budget, BudgetRun
from app.models.financial_profile import FinancialProfile
from app.models.transaction import Transaction
from app.services.budget_calculator import (
    build_history_plan, compute_spendable, is_essential, savings_target_amount,
)
from app.services.summary_service import UNCATEGORIZED_LABEL

logger = logging.getLogger(__name__)

_ZERO = Decimal("0.00")

# "Is this row spending?" as a SQL condition (see the header)
_EXPENSE_FILTER = or_(
    Transaction.txn_type == TXN_EXPENSE,
    and_(Transaction.txn_type.is_(None), Transaction.direction == DEBIT),
)


def _check_month(month: str) -> None:
    """The routes already validate the format; this protects other callers (scripts, evaluation)."""
    try:
        month_bounds(month)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None


# --- Financial profile ---

def get_profile(db: Session, user_id: int) -> FinancialProfile | None:
    """The user's profile, or None if they have not set one yet."""
    return db.query(FinancialProfile).filter(FinancialProfile.user_id == user_id).first()


def get_profile_or_404(db: Session, user_id: int) -> FinancialProfile:
    profile = get_profile(db, user_id)
    if profile is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No financial profile yet. Create one with PUT /api/profile/financial."
        )
    return profile


def upsert_profile(db: Session, user_id: int, monthly_income: Decimal, savings_target_pct: Decimal) -> FinancialProfile:
    """Creates the profile on first save, updates it afterwards (one row per user)."""
    profile = get_profile(db, user_id)
    if profile is None:
        profile = FinancialProfile(user_id=user_id)
        db.add(profile)
    profile.monthly_income = to_money(monthly_income)
    profile.savings_target_pct = to_money(savings_target_pct)
    db.commit()
    db.refresh(profile)
    logger.info(f"Financial profile saved user_id={user_id}")
    return profile


def profile_to_response(profile: FinancialProfile) -> dict:
    """The profile plus the two numbers derived from it."""
    return {
        "monthly_income": float(profile.monthly_income),
        "savings_target_pct": float(profile.savings_target_pct),
        "savings_target_amount": float(savings_target_amount(profile.monthly_income, profile.savings_target_pct)),
        "spendable_income": float(compute_spendable(profile.monthly_income, profile.savings_target_pct)),
        "updated_at": profile.updated_at,
    }


# --- History ---

def _history_averages(db: Session, user_id: int, month: str, lookback: int) -> tuple[dict[str, Decimal], int]:
    """
    Average monthly spending per expense category over the `lookback` full
    months before `month`. The divisor is the number of months that had ANY
    categorized expense, shared by all categories, so a one-off purchase is
    spread over the window instead of becoming a monthly limit.
    Grouping by month happens in Python, so it works the same on SQLite and Postgres.
    """
    start, end = lookback_window(month, lookback)
    rows = (
        db.query(Transaction.category, Transaction.amount, Transaction.transaction_date)
        .filter(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end,
            _EXPENSE_FILTER,
            Transaction.category.in_(CATEGORY_NAMES),
        )
        .all()
    )
    totals: dict[str, Decimal] = {}
    months: set[str] = set()
    for category, amount, moment in rows:
        totals[category] = totals.get(category, _ZERO) + to_money(amount)
        months.add(month_key(moment))
    if not months:
        return {}, 0
    divisor = Decimal(len(months))
    return {category: total / divisor for category, total in totals.items()}, len(months)


def _data_quality(db: Session, user_id: int, month: str, lookback: int, months_used: int) -> dict:
    """How trustworthy the lookback data is (shown with every plan)."""
    start, end = lookback_window(month, lookback)
    window = (
        Transaction.user_id == user_id,
        Transaction.transaction_date >= start,
        Transaction.transaction_date < end,
        _EXPENSE_FILTER,
    )
    uncategorized = db.query(func.count(Transaction.id)).filter(*window, Transaction.category.is_(None)).scalar()
    needs_review = db.query(func.count(Transaction.id)).filter(*window, Transaction.needs_review.is_(True)).scalar()
    transfers = db.query(func.sum(Transaction.amount)).filter(*window, Transaction.category == TRANSFERS_CATEGORY).scalar()
    return {
        "months_used": months_used,
        "uncategorized_count": uncategorized or 0,
        "needs_review_count": needs_review or 0,
        "transfers_total": float(to_money(transfers or 0)),
    }


# --- Budgets ---

def _build_planner() -> BudgetPlannerAgent:
    """The explanation agent. BUDGET_USE_LLM_EXPLANATIONS=false = templates only (ablation B1)."""
    if not settings.BUDGET_USE_LLM_EXPLANATIONS:
        return BudgetPlannerAgent(chains=[])
    return BudgetPlannerAgent.from_settings()


def list_budgets(db: Session, user_id: int, month: str) -> list[Budget]:
    """Every limit of one month, largest first."""
    _check_month(month)
    return (
        db.query(Budget)
        .filter(Budget.user_id == user_id, Budget.month == month)
        .order_by(Budget.limit_amount.desc(), Budget.category)
        .all()
    )


def _budget_to_dict(budget: Budget) -> dict:
    return {
        "id": budget.id,
        "month": budget.month,
        "category": budget.category,
        "limit_amount": float(budget.limit_amount),
        "agent_limit_amount": float(budget.agent_limit_amount),
        "source": budget.source,
        "reasoning": budget.reasoning,
    }


def _merge_into_budgets(
    db: Session, user_id: int, month: str, limits: dict[str, Decimal], reasons: dict[str, str]
) -> None:
    """
    Saves a new plan without losing the user's choices:
      * source='user' rows are left exactly as they are;
      * agent rows get the new limit (and agent_limit_amount) and reasoning;
      * new categories are inserted; agent rows no longer in the plan are deleted.
    """
    existing = {b.category: b for b in db.query(Budget).filter(Budget.user_id == user_id, Budget.month == month)}
    for category, limit in limits.items():
        row = existing.pop(category, None)
        if row is None:
            db.add(Budget(
                user_id=user_id, month=month, category=category, limit_amount=limit,
                agent_limit_amount=limit, source=SOURCE_AGENT, reasoning=reasons.get(category),
            ))
        elif row.source == SOURCE_AGENT:
            row.limit_amount = limit
            row.agent_limit_amount = limit
            row.reasoning = reasons.get(category)
        # else: the user's own limit is never touched by the planner
    for stale in existing.values():
        if stale.source == SOURCE_AGENT:
            db.delete(stale)


def generate_budget(db: Session, user_id: int, month: str) -> dict:
    """Builds (or rebuilds) the plan for `month` and returns it with its explanation."""
    _check_month(month)
    profile = get_profile(db, user_id)
    if profile is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Set your financial profile first (PUT /api/profile/financial)."
        )

    lookback = settings.BUDGET_LOOKBACK_MONTHS
    trim = Decimal(str(settings.BUDGET_DISCRETIONARY_TRIM))
    income, pct = profile.monthly_income, profile.savings_target_pct

    # 1. Python does all the maths
    averages, months_used = _history_averages(db, user_id, month, lookback)
    plan = build_history_plan(income, pct, averages, trim)
    savings = savings_target_amount(income, pct)

    # 2. The agent explains the finished numbers (one LLM call at most)
    facts = [
        BudgetFact(category, limit, to_money(averages.get(category, _ZERO)), is_essential(category))
        for category, limit in sorted(plan.limits.items(), key=lambda item: (-item[1], item[0]))
    ]
    context = PlanContext(
        month=month, method=plan.method, income=income, savings_target_pct=pct, savings_amount=savings,
        spendable=plan.spendable, feasible=plan.feasible, shortfall=plan.shortfall, months_used=months_used,
        trim=trim, discretionary_scale=plan.discretionary_scale,
    )
    explanation = _build_planner().explain(context, facts)

    # 3. Save the limits and the research log in one transaction
    _merge_into_budgets(db, user_id, month, plan.limits, explanation.reasons)
    db.add(BudgetRun(
        user_id=user_id, month=month, method=plan.method, spendable=plan.spendable, feasible=plan.feasible,
        shortfall=plan.shortfall, months_used=months_used, llm_used=explanation.llm_used,
        explanation_faithful=explanation.faithful,
    ))
    db.commit()
    logger.info(
        f"Budget generated user_id={user_id} month={month} method={plan.method} feasible={plan.feasible} "
        f"categories={len(plan.limits)} llm_used={explanation.llm_used} faithful={explanation.faithful}"
    )

    return {
        "month": month,
        "method": plan.method,
        "monthly_income": float(income),
        "savings_target_amount": float(savings),
        "spendable_income": float(plan.spendable),
        "feasible": plan.feasible,
        "shortfall": float(plan.shortfall),
        "summary": explanation.summary,
        "llm_used": explanation.llm_used,
        "explanation_faithful": explanation.faithful,
        "data_quality": _data_quality(db, user_id, month, lookback, months_used),
        "budgets": [_budget_to_dict(b) for b in list_budgets(db, user_id, month)],
    }


def _get_owned_budget(db: Session, user_id: int, budget_id: int) -> Budget:
    """One of the user's budgets. Someone else's id is a 404, so we don't reveal it exists."""
    budget = db.query(Budget).filter(Budget.id == budget_id, Budget.user_id == user_id).first()
    if budget is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Budget not found.")
    return budget


def override_budget(db: Session, user_id: int, budget_id: int, limit_amount: Decimal) -> Budget:
    """The user sets their own limit. agent_limit_amount is kept (override-rate metric, reset)."""
    budget = _get_owned_budget(db, user_id, budget_id)
    budget.limit_amount = to_money(limit_amount)
    budget.source = SOURCE_USER
    db.commit()
    db.refresh(budget)
    logger.info(f"Budget overridden id={budget_id} user_id={user_id}")
    return budget


def reset_budget(db: Session, user_id: int, budget_id: int) -> Budget:
    """Back to the planner's number; the next regeneration may refresh it again."""
    budget = _get_owned_budget(db, user_id, budget_id)
    budget.limit_amount = budget.agent_limit_amount
    budget.source = SOURCE_AGENT
    db.commit()
    db.refresh(budget)
    logger.info(f"Budget reset id={budget_id} user_id={user_id}")
    return budget


# --- Status (actual vs limit) ---

def _status_item(category: str, limit: Decimal, spent: Decimal, threshold: Decimal, source: str) -> dict:
    """ok < threshold <= warning <= 100% < exceeded. A limit of 0 with any spending is exceeded."""
    if spent > limit:
        state = "exceeded"
    elif limit > 0 and spent >= threshold * limit:
        state = "warning"
    else:
        state = "ok"
    return {
        "category": category,
        "limit": float(limit),
        "spent": float(spent),
        "remaining": float(limit - spent),
        "percent_used": round(float(spent / limit * 100), 1) if limit > 0 else None,
        "status": state,
        "source": source,
    }


def budget_status(db: Session, user_id: int, month: str) -> dict:
    """Spending of `month` per category against its limits, plus unbudgeted spending."""
    _check_month(month)
    start, end = month_bounds(month)
    in_month = (
        Transaction.user_id == user_id,
        Transaction.transaction_date >= start,
        Transaction.transaction_date < end,
    )

    spent: dict[str, Decimal] = {}
    for category, total in (
        db.query(Transaction.category, func.sum(Transaction.amount))
        .filter(*in_month, _EXPENSE_FILTER)
        .group_by(Transaction.category)
        .all()
    ):
        label = category or UNCATEGORIZED_LABEL
        spent[label] = spent.get(label, _ZERO) + to_money(total or 0)

    total_spent = sum(spent.values(), _ZERO)   # before the loop below pops the budgeted ones
    threshold = Decimal(str(settings.BUDGET_WARNING_THRESHOLD))
    budgets = list_budgets(db, user_id, month)
    items = [
        _status_item(b.category, b.limit_amount, spent.pop(b.category, _ZERO), threshold, b.source)
        for b in budgets
    ]
    # Spending in categories that have no limit this month
    for category, amount in sorted(spent.items(), key=lambda item: (-item[1], item[0])):
        items.append({
            "category": category, "limit": None, "spent": float(amount), "remaining": None,
            "percent_used": None, "status": "unbudgeted", "source": None,
        })

    invested = db.query(func.sum(Transaction.amount)).filter(
        *in_month, Transaction.txn_type == TXN_INVESTMENT_OUT
    ).scalar()
    profile = get_profile(db, user_id)

    total_limit = sum((b.limit_amount for b in budgets), _ZERO)
    return {
        "month": month,
        "items": items,
        "total_limit": float(total_limit),
        "total_spent": float(total_spent),
        "total_remaining": float(total_limit - total_spent),
        "invested_this_month": float(to_money(invested or 0)),
        "savings_target_amount": (
            float(savings_target_amount(profile.monthly_income, profile.savings_target_pct)) if profile else None
        ),
    }
