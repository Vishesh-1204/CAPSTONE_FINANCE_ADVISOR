# app/api/routes/forecast.py
#
# Purpose: HTTP endpoints of the Financial Forecast Agent (Phase 5).
# HTTP only: the logic lives in services/forecast_service.py.
#
# horizon_days arrives as text in the URL, so it is read as an int and the
# service rejects anything but 30/60/90 with a 422 (a Literal[30, 60, 90] query
# parameter does not convert the text "60" and would reject every value).
# as_of is optional (default: today, UTC) and may not be in the future; it
# exists so a forecast can be reproduced or demoed on past data.
# Fixed paths (/generate, /latest, /history, /recurring) never clash with /runs/{run_id}.

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.forecast import HORIZON_CHOICES, ForecastHistoryItem, ForecastRunResponse, RecurringListResponse
from app.services.forecast_service import (
    generate_forecast, get_run, latest_run, list_runs, recurring_overview, resolve_as_of,
)

router = APIRouter(prefix="/api/forecast", tags=["Forecast"])

# Shown in Swagger as a dropdown; enforced by forecast_service (422 for anything else)
_HORIZON_SCHEMA = {"enum": list(HORIZON_CHOICES)}


@router.post("/generate", response_model=ForecastRunResponse)
def generate(
    horizon_days: int = Query(30, description="30, 60 or 90", json_schema_extra=_HORIZON_SCHEMA),
    opening_balance: Decimal | None = Query(
        None, max_digits=14, decimal_places=2,
        description="Your current balance. Leave empty to use the latest balance on an imported statement.",
    ),
    as_of: date | None = Query(None, description="Forecast as if today were this date (default: today)."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Forecasts income, fixed bills, everyday spending, investments and your daily
    balance for the next 30/60/90 days, with an 80% range. The run is saved.
    """
    return generate_forecast(
        db, user_id=current_user.id, horizon_days=horizon_days, as_of=resolve_as_of(as_of),
        opening_balance=opening_balance,
    )


@router.get("/latest", response_model=ForecastRunResponse)
def latest(
    horizon_days: int | None = Query(None, description="Only runs of this horizon (30, 60 or 90)",
                                     json_schema_extra=_HORIZON_SCHEMA),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Your most recent saved forecast."""
    return latest_run(db, user_id=current_user.id, horizon_days=horizon_days)


@router.get("/history", response_model=list[ForecastHistoryItem])
def history(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Your saved forecasts, newest first (summary only)."""
    return list_runs(db, user_id=current_user.id, limit=limit)


@router.get("/recurring", response_model=RecurringListResponse)
def recurring(
    as_of: date | None = Query(None, description="Detect as if today were this date (default: today)."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Salary, rent, subscriptions and SIPs detected in your history (read-only)."""
    return recurring_overview(db, user_id=current_user.id, as_of=resolve_as_of(as_of))


@router.get("/runs/{run_id}", response_model=ForecastRunResponse)
def run_by_id(
    run_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One of your saved forecasts."""
    return get_run(db, user_id=current_user.id, run_id=run_id)
