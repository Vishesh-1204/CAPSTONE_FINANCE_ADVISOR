# app/api/routes/investing.py
#
# Purpose: HTTP endpoints of the Investment Advisor Agent (Phase 6).
# HTTP only: the logic lives in services/investment_service.py.
#
# Every route needs a JWT, and the user is ALWAYS the one in the token
# (get_current_user), never a field of the request. Every response carries a
# `disclaimer` (schemas/investment.py adds it): educational tool, not advice.
# as_of is optional (default: today, UTC) and may not be in the future; it exists
# so a plan can be reproduced or demoed on past data, like the forecast routes.
# POST /simulate is a what-if: nothing is stored, and the number of Monte Carlo
# paths comes from settings (a client cannot ask for a billion paths).

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.investment import (
    AssumptionsResponse, HoldingsResponse, HoldingsUpdate, InvestmentPlanResponse, InvestmentProfileResponse,
    InvestmentProfileUpdate, PlanHistoryResponse, PlanRequest, SimulateRequest, SimulateResponse,
)
from app.services import investment_service as svc
from app.services.forecast_service import resolve_as_of

router = APIRouter(prefix="/api/investing", tags=["Investing"])

_AS_OF = "Act as if today were this date (default: today)."


# --- Risk profile ---

@router.put("/profile", response_model=InvestmentProfileResponse)
def save_profile(
    body: InvestmentProfileUpdate,
    as_of: date | None = Query(None, description=_AS_OF),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Saves the risk questionnaire and your finance facts, and returns the risk
    profile: tolerance (attitude) vs capacity (finances), the safer one wins.
    """
    return svc.upsert_profile(db, current_user.id, body, resolve_as_of(as_of))


@router.get("/profile", response_model=InvestmentProfileResponse)
def read_profile(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Your saved risk profile (404 until you save one)."""
    return svc.get_profile(db, current_user.id)


# --- Holdings ---

@router.put("/holdings", response_model=HoldingsResponse)
def save_holdings(
    body: HoldingsUpdate,
    as_of: date | None = Query(None, description="Date for holdings sent without one (default: today)."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """REPLACES your whole holdings list (rupees per asset class). An empty list clears it."""
    return svc.replace_holdings(db, current_user.id, body.holdings, resolve_as_of(as_of))


@router.get("/holdings", response_model=HoldingsResponse)
def read_holdings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Your holdings with each one's share of the total."""
    return svc.get_holdings(db, current_user.id)


# --- Plans ---

@router.post("/plan", response_model=InvestmentPlanResponse)
def generate_plan(
    body: PlanRequest | None = None,
    as_of: date | None = Query(None, description=_AS_OF),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Builds and saves a plan: risk profile, target allocation and rebalancing,
    an affordable SIP from your forecast's low-end surplus (after the emergency
    fund), and a range of simulated outcomes. Needs a risk profile (409 without).
    """
    request = body or PlanRequest()
    return svc.generate_plan(
        db, current_user.id, resolve_as_of(as_of), goal_amount=request.goal_amount, goal_years=request.goal_years
    )


@router.get("/plan/latest", response_model=InvestmentPlanResponse)
def latest_plan(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Your most recent saved plan (404 before the first one)."""
    return svc.latest_plan(db, current_user.id)


@router.get("/plans", response_model=PlanHistoryResponse)
def plan_history(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Your saved plans, newest first (summary only)."""
    return svc.list_plans(db, current_user.id, limit)


@router.get("/plans/{plan_id}", response_model=InvestmentPlanResponse)
def plan_by_id(plan_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """One of your saved plans in full (404 if it does not exist or is not yours)."""
    return svc.get_plan(db, current_user.id, plan_id)


# --- What-if and assumptions (nothing stored) ---

@router.post("/simulate", response_model=SimulateResponse)
def simulate(body: SimulateRequest, current_user: User = Depends(get_current_user)):
    """What-if: any SIP, years (1-40) and risk category under the current assumptions. Not saved."""
    return svc.simulate(body)


@router.get("/assumptions", response_model=AssumptionsResponse)
def assumptions(current_user: User = Depends(get_current_user)):
    """The return and volatility assumptions every projection uses (illustrative placeholders)."""
    return svc.get_assumptions()
