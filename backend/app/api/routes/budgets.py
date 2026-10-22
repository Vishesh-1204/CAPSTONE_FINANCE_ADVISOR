# app/api/routes/budgets.py
#
# Purpose: HTTP endpoints of the Budget Planning Agent: generate a monthly
# plan, list it, override/reset one limit, and compare spending to limits.
# HTTP only: the logic lives in services/budget_service.py.
#
# Route order matters: fixed paths (/generate, /status) are declared before
# /{budget_id}/... so a word is never read as an id.

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.month_utils import MONTH_PATTERN
from app.db.session import get_db
from app.models.user import User
from app.schemas.budget import BudgetItem, BudgetPlanResponse, BudgetStatusResponse, BudgetUpdate
from app.services.budget_service import (
    budget_status, generate_budget, list_budgets, override_budget, reset_budget,
)

router = APIRouter(prefix="/api/budgets", tags=["Budgets"])

# Required ?month=YYYY-MM; '2026-13' or '26-09' is rejected with a 422 before any code runs
Month = Annotated[str, Query(pattern=MONTH_PATTERN, description="YYYY-MM", examples=["2026-10"])]


@router.post("/generate", response_model=BudgetPlanResponse)
def generate(
    month: Month,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Builds the plan for `month` from your expenses in the months before it.
    Regenerating keeps the limits you set yourself.
    """
    return generate_budget(db, user_id=current_user.id, month=month)


@router.get("/status", response_model=BudgetStatusResponse)
def status_for_month(
    month: Month,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Actual spending of `month` (expenses only) against each limit."""
    return budget_status(db, user_id=current_user.id, month=month)


@router.get("/", response_model=list[BudgetItem])
def get_budgets(
    month: Month,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Your limits for `month`, largest first."""
    return list_budgets(db, user_id=current_user.id, month=month)


@router.put("/{budget_id}", response_model=BudgetItem)
def set_budget_limit(
    budget_id: int,
    payload: BudgetUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sets your own limit for one category (regeneration will not change it)."""
    return override_budget(db, user_id=current_user.id, budget_id=budget_id, limit_amount=payload.limit_amount)


@router.post("/{budget_id}/reset", response_model=BudgetItem)
def reset_budget_limit(
    budget_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Puts the planner's original limit back."""
    return reset_budget(db, user_id=current_user.id, budget_id=budget_id)
