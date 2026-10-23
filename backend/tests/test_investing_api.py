# tests/test_investing_api.py
#
# HTTP-level tests for /api/investing/... (Phase 6A-5). SQLite in memory, login
# bypassed for most tests (the current user can be switched), a template-only
# explainer (no API key or network), and few Monte Carlo paths for speed.
# The auth tests use the REAL get_current_user, so a missing or bad token is a 401.

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.investment_explainer import InvestmentExplainer
from app.api.deps import get_current_user
from app.api.routes import investing as investing_routes
from app.core.config import settings
from app.core.disclaimers import INVESTING_DISCLAIMER
from app.db.session import get_db
from app.models.budget import Budget
from app.models.investment import Holding, InvestmentPlan, InvestmentProfile
from app.models.user import User
from app.services import investment_service as svc
from app.services.investing.claims_guard import find_prohibited_claims
from tests.conftest import add_forecast_run

AS_OF = "2026-10-23"
PROFILE = {
    "answers": {"reaction_to_20pct_drop": 5, "investing_experience": 4, "return_vs_safety": 5},
    "liquid_savings": 200000, "existing_monthly_sip": 2000, "income_stability": 5, "dependents": 0,
    "horizon_years": 12,
}
HOLDINGS = {"holdings": [
    {"asset_class": "equity", "amount": 90000, "label": "index fund"},
    {"asset_class": "debt", "amount": 10000},
]}
SIMULATE = {"monthly_sip": 10000, "years": 10, "category": "moderate", "goal_amount": 2000000}

# Every endpoint: (method, path, JSON body)
ENDPOINTS = [
    ("PUT", "/api/investing/profile", PROFILE),
    ("GET", "/api/investing/profile", None),
    ("PUT", "/api/investing/holdings", HOLDINGS),
    ("GET", "/api/investing/holdings", None),
    ("POST", "/api/investing/plan", {}),
    ("GET", "/api/investing/plan/latest", None),
    ("GET", "/api/investing/plans", None),
    ("POST", "/api/investing/simulate", SIMULATE),
    ("GET", "/api/investing/assumptions", None),
]


@pytest.fixture(autouse=True)
def fast_and_offline(monkeypatch):
    monkeypatch.setattr(settings, "INVEST_MC_PATHS", 200)
    monkeypatch.setattr(svc, "_build_explainer", lambda: InvestmentExplainer(find_prohibited_claims, chains=[]))


@pytest.fixture()
def current(db):
    """Which user the API thinks is logged in (tests can switch to user 2)."""
    return {"user_id": 1}


def _app(db) -> FastAPI:
    app = FastAPI()
    app.include_router(investing_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.fixture()
def client(db, current):
    app = _app(db)
    app.dependency_overrides[get_current_user] = lambda: db.get(User, current["user_id"])
    return TestClient(app)


@pytest.fixture()
def anonymous(db):
    """The real get_current_user: needs a valid Bearer token."""
    return TestClient(_app(db))


def call(client, method, path, body=None, status=200, **params):
    response = client.request(method, path, json=body, params=params or None)
    assert response.status_code == status, response.text
    return response.json()


def essentials_and_forecast(db, user_id=1):
    """Rent + groceries = 20,000 a month of essentials, and a fresh 30-day forecast."""
    for category, amount in (("Housing & Rent", "15000"), ("Groceries", "5000")):
        db.add(Budget(user_id=user_id, month="2026-10", category=category, limit_amount=Decimal(amount),
                      agent_limit_amount=Decimal(amount)))
    db.commit()
    add_forecast_run(db, user_id=user_id)


def full_setup(client, db):
    essentials_and_forecast(db)
    call(client, "PUT", "/api/investing/profile", PROFILE, as_of=AS_OF)
    call(client, "PUT", "/api/investing/holdings", HOLDINGS, as_of=AS_OF)


# --- Auth ---

@pytest.mark.parametrize("method, path, body", ENDPOINTS)
def test_every_route_needs_a_login(anonymous, method, path, body):
    call(anonymous, method, path, body, status=401)


def test_a_bad_token_is_401(anonymous):
    response = anonymous.get("/api/investing/profile", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


# --- Disclaimer ---

def test_every_successful_response_carries_the_disclaimer(client, db):
    full_setup(client, db)
    call(client, "POST", "/api/investing/plan", {}, as_of=AS_OF)
    for method, path, body in ENDPOINTS:
        params = {"as_of": AS_OF} if method in ("PUT", "POST") and path != "/api/investing/simulate" else {}
        assert call(client, method, path, body, **params)["disclaimer"] == INVESTING_DISCLAIMER, path


# --- Profile and holdings ---

def test_profile_round_trip(client, db):
    essentials_and_forecast(db)
    call(client, "GET", "/api/investing/profile", status=404)
    saved = call(client, "PUT", "/api/investing/profile", PROFILE, as_of=AS_OF)
    assert saved["emergency_fund_months"] == 10.0                   # 2,00,000 / 20,000
    assert saved["risk_profile"]["category"] == "aggressive"
    assert call(client, "GET", "/api/investing/profile") == saved


def test_holdings_are_replaced_and_weighted(client):
    body = call(client, "PUT", "/api/investing/holdings", HOLDINGS, as_of=AS_OF)
    assert body["total"] == 100000 and body["weights_by_class"]["equity"] == 0.9
    assert all(row["as_of"] == AS_OF for row in body["holdings"])
    assert call(client, "PUT", "/api/investing/holdings", {"holdings": []})["holdings"] == []
    assert call(client, "GET", "/api/investing/holdings")["total"] == 0


# --- Plans ---

def test_plan_needs_a_profile_first(client):
    call(client, "POST", "/api/investing/plan", {}, status=409)


def test_plan_flow_latest_and_history(client, db):
    full_setup(client, db)
    plan = call(client, "POST", "/api/investing/plan", {"goal_amount": 1000000, "goal_years": 8}, as_of=AS_OF)
    assert plan["risk_profile"]["category"] == "aggressive"
    assert plan["current_allocation"]["equity"] == 0.9
    assert plan["surplus"]["source"] == "forecast" and plan["forecast_run_id"] is not None
    assert plan["goal"]["years"] == 8 and plan["projection_recommended"]["probability_of_goal"] is not None
    assert plan["explanation_source"] == "template" and plan["explanation"].endswith(INVESTING_DISCLAIMER)
    assert {row["asset_class"] for row in plan["instruments"]} == {"equity", "debt", "gold", "cash"}
    assert plan["assumptions"]["note"] and len(plan["assumptions"]["assets"]) == 4

    assert call(client, "GET", "/api/investing/plan/latest")["id"] == plan["id"]
    history = call(client, "GET", "/api/investing/plans")["plans"]
    assert [item["id"] for item in history] == [plan["id"]]
    assert history[0]["recommended_sip"] == plan["sip"]["recommended_total"]


def test_plan_without_a_body_uses_the_profile_horizon(client, db):
    full_setup(client, db)
    plan = call(client, "POST", "/api/investing/plan", as_of=AS_OF)
    assert plan["goal"] == {"amount": None, "years": 12.0, "required_sip": None}


def test_latest_plan_is_404_before_the_first_one(client):
    call(client, "GET", "/api/investing/plan/latest", status=404)


# --- Validation ---

@pytest.mark.parametrize("method, path, body", [
    ("PUT", "/api/investing/profile", {**PROFILE, "answers": {**PROFILE["answers"], "return_vs_safety": 6}}),
    ("PUT", "/api/investing/profile", {**PROFILE, "liquid_savings": -1}),
    ("PUT", "/api/investing/profile", {k: v for k, v in PROFILE.items() if k != "answers"}),
    ("PUT", "/api/investing/holdings", {"holdings": [{"asset_class": "crypto", "amount": 1000}]}),
    ("PUT", "/api/investing/holdings", {"holdings": [{"asset_class": "gold", "amount": 1},
                                                     {"asset_class": "gold", "amount": 2}]}),
    ("POST", "/api/investing/plan", {"goal_years": 5}),
    ("POST", "/api/investing/plan", {"goal_amount": 0}),
    ("POST", "/api/investing/simulate", {**SIMULATE, "years": 41}),
    ("POST", "/api/investing/simulate", {**SIMULATE, "years": 0.5}),
    ("POST", "/api/investing/simulate", {**SIMULATE, "monthly_sip": 10000001}),
    ("POST", "/api/investing/simulate", {**SIMULATE, "category": "reckless"}),
])
def test_invalid_requests_are_422(client, method, path, body):
    call(client, method, path, body, status=422)


def test_future_as_of_is_422(client):
    tomorrow = (date.today() + timedelta(days=2)).isoformat()
    call(client, "PUT", "/api/investing/profile", PROFILE, status=422, as_of=tomorrow)


def test_plans_limit_is_bounded(client):
    call(client, "GET", "/api/investing/plans", status=422, limit=0)
    call(client, "GET", "/api/investing/plans", status=422, limit=101)


# --- Security ---

def test_user_id_in_the_body_is_ignored(client, db):
    call(client, "PUT", "/api/investing/profile", {**PROFILE, "user_id": 2}, as_of=AS_OF)
    call(client, "PUT", "/api/investing/holdings", {**HOLDINGS, "user_id": 2}, as_of=AS_OF)
    assert [p.user_id for p in db.query(InvestmentProfile).all()] == [1]
    assert {h.user_id for h in db.query(Holding).all()} == {1}


def test_user_b_cannot_see_or_change_user_a_data(client, db, current):
    full_setup(client, db)
    plan_a = call(client, "POST", "/api/investing/plan", {}, as_of=AS_OF)

    current["user_id"] = 2
    call(client, "GET", "/api/investing/profile", status=404)
    call(client, "GET", "/api/investing/plan/latest", status=404)
    call(client, "POST", "/api/investing/plan", {}, status=409)        # A's profile is not B's
    assert call(client, "GET", "/api/investing/plans")["plans"] == []
    assert call(client, "GET", "/api/investing/holdings")["holdings"] == []
    call(client, "PUT", "/api/investing/holdings", {"holdings": []})  # B clearing "their" list ...

    current["user_id"] = 1
    assert call(client, "GET", "/api/investing/holdings")["total"] == 100000    # ... leaves A's alone
    assert call(client, "GET", "/api/investing/plan/latest")["id"] == plan_a["id"]


# --- What-if and assumptions ---

def test_simulate_is_not_stored_and_paths_come_from_settings(client, db):
    body = call(client, "POST", "/api/investing/simulate", {**SIMULATE, "n_paths": 10**9})
    assert body["projection"]["n_paths"] == settings.INVEST_MC_PATHS     # the client cannot choose
    assert body["projection"]["p10"] <= body["projection"]["p50"] <= body["projection"]["p90"]
    assert db.query(InvestmentPlan).count() == 0


def test_assumptions_are_listed_with_their_placeholder_note(client):
    body = call(client, "GET", "/api/investing/assumptions")
    assert [a["asset_class"] for a in body["assets"]] == ["equity", "debt", "gold", "cash"]
    assert "placeholder" in body["note"].lower() and body["mc_paths"] == settings.INVEST_MC_PATHS
