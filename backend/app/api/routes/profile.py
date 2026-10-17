# app/api/routes/profile.py
#
# Purpose: HTTP endpoints for the user's financial profile (income and
# savings target), the input of the Budget Planning Agent. HTTP only: the
# logic lives in services/budget_service.py.

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.month_utils import MONTH_PATTERN
from app.db.session import get_db
from app.models.user import User
from app.schemas.budget import FinancialProfileResponse, FinancialProfileUpdate, IncomeSuggestion
from app.services.budget_service import get_profile_or_404, profile_to_response, suggest_income, upsert_profile

router = APIRouter(prefix="/api/profile", tags=["Profile"])


@router.get("/financial", response_model=FinancialProfileResponse)
def read_financial_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Your income and savings target (404 until you save one)."""
    return profile_to_response(get_profile_or_404(db, user_id=current_user.id))


@router.put("/financial", response_model=FinancialProfileResponse)
def save_financial_profile(
    payload: FinancialProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Creates or updates your income and savings target (percent of income)."""
    profile = upsert_profile(
        db, user_id=current_user.id,
        monthly_income=payload.monthly_income, savings_target_pct=payload.savings_target_pct,
    )
    return profile_to_response(profile)


@router.get("/financial/suggestion", response_model=IncomeSuggestion)
def suggest_monthly_income(
    month: Annotated[
        str | None, Query(pattern=MONTH_PATTERN, description="YYYY-MM (default: this month)", examples=["2026-10"])
    ] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Suggests a monthly income from your income credits in the months before
    `month` (refunds excluded). Nothing is saved: use PUT /financial to keep it.
    """
    return suggest_income(db, user_id=current_user.id, month=month)
