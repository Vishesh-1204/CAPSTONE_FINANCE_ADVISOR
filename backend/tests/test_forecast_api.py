# tests/test_forecast_api.py
#
# HTTP-level tests for /api/forecast/... (spec section 7). SQLite in memory,
# login bypassed (the current user can be switched), and the explainer is a
# template-only or fake-LLM explainer, so no API key or network is needed.

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.forecast_explainer import ForecastExplainer
from app.api.deps import get_current_user
from app.api.routes import forecast as forecast_routes
from app.db.session import get_db
from app.models.budget import Budget
from app.models.financial_profile import FinancialProfile
from app.models.forecast import ForecastRun
from app.models.user import User
from app.services import forecast_service as svc
from tests.conftest import FORECAST_AS_OF, add_txn, seed_forecast_persona

AS_OF = FORECAST_AS_OF.isoformat()          # 2026-10-22
LONG_HISTORY = ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09")
LONG_GROCERIES = (5000, 5200, 4800, 5100, 4900, 5300, 5000, 5100, 4950, 2000)


class FakeChain:
    def __init__(self, answer: str | Exception):
        self.answer = answer

    def invoke(self, inputs: dict) -> str:
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture()
def current(db):
    """Which user the API thinks is logged in (tests can switch to user 2)."""
    return {"user_id": 1}


@pytest.fixture()
def client(db, current, monkeypatch):
    monkeypatch.setattr(svc, "_build_explainer", lambda: ForecastExplainer(chains=[]))
    app = FastAPI()
    app.include_router(forecast_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(User, current["user_id"])
    return TestClient(app)


@pytest.fixture()
def seeded(db):
    seed_forecast_persona(db)
    return db


def generate(client, status=200, **params):
    params.setdefault("as_of", AS_OF)
    response = client.post("/api/forecast/generate", params=params)
    assert response.status_code == status, response.text
    return response.json()


# --- the main forecast ---

def test_generate_projects_salary_rent_and_sip_on_their_dates(client, seeded):
    body = generate(client)
    assert body["horizon_days"] == 30 and body["as_of"] == AS_OF
    assert body["expected_income"] == 60000.0            # salary on 1 Nov
    assert body["expected_fixed_expenses"] == 18000.0    # rent on 5 Nov
    assert body["expected_investments"] == 3000.0        # SIP on 10 Nov, reported separately
    assert body["income_source"] == "recurring"
    kinds = {item["kind"]: item for item in body["recurring_items"]}
    assert kinds["income"]["projected_dates"] == ["2026-11-01"]
    assert kinds["fixed_expense"]["projected_dates"] == ["2026-11-05"]
    assert kinds["investment"]["projected_dates"] == ["2026-11-10"]


def test_rent_is_not_counted_twice(client, seeded):
    body = generate(client)
    # Variable = groceries only: 3-month average 6,000 x (9/31 October + 21/30 November)
    assert body["expected_variable_expenses"]["point"] == pytest.approx(6000 * (9 / 31 + 21 / 30), abs=0.01)


def test_balance_arithmetic_and_trajectory(client, seeded):
    body = generate(client)
    variable = body["expected_variable_expenses"]["point"]
    assert body["opening_balance"] == 100004.0 and body["opening_balance_source"] == "statement"
    assert body["opening_balance_date"] == "2026-10-12"
    assert body["net_cashflow"] == pytest.approx(60000 - 18000 - 3000 - variable, abs=0.01)
    assert body["projected_end_balance"]["point"] == pytest.approx(100004 + body["net_cashflow"], abs=0.02)
    trajectory = body["trajectory"]
    assert len(trajectory) == 30
    assert trajectory[0]["date"] == "2026-10-23" and trajectory[-1]["date"] == "2026-11-21"
    assert trajectory[-1]["balance"] == pytest.approx(body["projected_end_balance"]["point"], abs=0.02)
    band = body["projected_end_balance"]
    assert band["lower"] <= band["point"] <= band["upper"]
    assert body["cash_crunch_date"] is None and body["cash_crunch_basis"] == "lower"


def test_run_is_saved(client, seeded, db):
    body = generate(client)
    run = db.get(ForecastRun, body["id"])
    assert run.user_id == 1 and run.horizon_days == 30 and run.llm_used is False
    assert len(run.trajectory) == 30


@pytest.mark.parametrize("days", [60, 90])
def test_longer_horizons(client, seeded, days):
    body = generate(client, horizon_days=days)
    assert len(body["trajectory"]) == days
    assert body["expected_income"] == 60000.0 * (2 if days == 60 else 3)    # 1 Nov, 1 Dec (, 1 Jan)


# --- history length and confidence ---

def test_four_months_is_low_confidence_with_the_fallback_model(client, seeded):
    body = generate(client)
    assert body["low_confidence"] and body["model_name"] == "moving_average_3" and body["model_scores"] == []
    assert body["history_months"] == 4


def test_two_months_is_low_confidence(client, db):
    seed_forecast_persona(db, history=("2026-08", "2026-09"))
    body = generate(client)
    assert body["low_confidence"] and body["history_months"] == 2


def test_nine_months_selects_a_model_by_backtest(client, db):
    seed_forecast_persona(db, history=LONG_HISTORY, groceries=LONG_GROCERIES)
    body = generate(client)
    assert not body["low_confidence"] and body["history_months"] == 9
    assert {s["model_name"] for s in body["model_scores"]} >= {"naive_last", "moving_average_3", "exp_smoothing"}


def test_less_than_one_complete_month_is_422(client, db):
    add_txn(db, date(2026, 10, 3), 500, category="Groceries", balance=1000)
    generate(client, status=422)


# --- opening balance ---

def test_requested_opening_balance_wins(client, seeded):
    body = generate(client, opening_balance="2500.50")
    assert body["opening_balance"] == 2500.5 and body["opening_balance_source"] == "request"
    assert body["opening_balance_date"] is None


def test_no_balance_anywhere_is_400(client, db):
    add_txn(db, date(2026, 8, 10), 500, category="Groceries")
    add_txn(db, date(2026, 9, 10), 700, category="Groceries")
    generate(client, status=400)
    assert generate(client, opening_balance=1000)["opening_balance_source"] == "request"


def test_low_opening_balance_gives_a_cash_crunch(client, seeded):
    body = generate(client, opening_balance=0)
    assert body["cash_crunch_date"] == "2026-10-23"
    assert "Warning" in body["summary"]


# --- income source ---

def test_profile_income_is_spread_evenly_when_no_salary_is_detected(client, db):
    add_txn(db, date(2026, 8, 10), 5000, category="Groceries")
    add_txn(db, date(2026, 9, 10), 5000, category="Groceries", balance=20000)
    db.add(FinancialProfile(user_id=1, monthly_income=Decimal("45000")))
    db.commit()
    body = generate(client)
    assert body["income_source"] == "profile"
    assert body["expected_income"] == pytest.approx(45000 * (9 / 31 + 21 / 30), abs=0.05)


# --- budget link ---

def test_budget_warnings_include_month_to_date_and_scheduled_rent(client, seeded, db):
    for month, category, limit in (("2026-10", "Groceries", 3000), ("2026-10", "Housing & Rent", 20000),
                                   ("2026-11", "Groceries", 1000), ("2026-11", "Housing & Rent", 15000)):
        db.add(Budget(user_id=1, month=month, category=category, limit_amount=Decimal(limit),
                      agent_limit_amount=Decimal(limit)))
    db.commit()
    warnings = {(w["month"], w["category"]): w for w in generate(client)["budget_warnings"]}
    # October groceries: 2,000 already spent + 6,000 x 9/31 still to come
    assert warnings[("2026-10", "Groceries")]["projected"] == pytest.approx(2000 + 6000 * 9 / 31, abs=0.01)
    assert ("2026-10", "Housing & Rent") not in warnings          # 18,000 paid, nothing more due
    assert warnings[("2026-11", "Groceries")]["projected"] == pytest.approx(6000, abs=0.01)
    assert warnings[("2026-11", "Housing & Rent")]["over_by"] == pytest.approx(3000)   # rent due 5 Nov


def test_no_budgets_means_no_warnings(client, seeded):
    assert generate(client)["budget_warnings"] == []


# --- explanation and LLM ---

def test_llm_down_uses_the_template(client, seeded, monkeypatch):
    monkeypatch.setattr(svc, "_build_explainer", lambda: ForecastExplainer(chain=FakeChain(RuntimeError("quota"))))
    body = generate(client)
    assert body["llm_used"] is False and body["explanation_faithful"] is None
    assert body["summary"].startswith("Over the next 30 days")


def test_llm_inventing_an_amount_is_replaced_by_the_template(client, seeded, monkeypatch):
    invented = json.dumps({"summary": "You will save ₹99,999."})
    monkeypatch.setattr(svc, "_build_explainer", lambda: ForecastExplainer(chain=FakeChain(invented)))
    body = generate(client)
    assert body["llm_used"] is True and body["explanation_faithful"] is False
    assert "99,999" not in body["summary"]


def test_honest_llm_text_is_kept(client, seeded, monkeypatch):
    honest = json.dumps({"summary": "Your salary of ₹60,000 arrives on 1 November and rent of ₹18,000 leaves on the 5th."})
    monkeypatch.setattr(svc, "_build_explainer", lambda: ForecastExplainer(chain=FakeChain(honest)))
    body = generate(client)
    assert body["llm_used"] and body["explanation_faithful"] and body["summary"].startswith("Your salary")


# --- validation ---

@pytest.mark.parametrize("horizon", ["45", "0", "365", "abc"])
def test_invalid_horizon_is_422(client, seeded, horizon):
    generate(client, status=422, horizon_days=horizon)


def test_invalid_horizon_on_latest_is_422(client, seeded):
    assert client.get("/api/forecast/latest", params={"horizon_days": 45}).status_code == 422


def test_future_as_of_is_422(client, seeded):
    generate(client, status=422, as_of=(date.today() + timedelta(days=5)).isoformat())


def test_same_as_of_gives_the_same_forecast(client, seeded):
    first, second = generate(client), generate(client)
    for body in (first, second):
        body.pop("id"), body.pop("created_at")
    assert first == second


# --- saved runs and isolation ---

def test_latest_history_and_run_by_id(client, seeded):
    first = generate(client, horizon_days=30)
    second = generate(client, horizon_days=60)
    assert client.get("/api/forecast/latest").json()["id"] == second["id"]
    assert client.get("/api/forecast/latest", params={"horizon_days": 30}).json()["id"] == first["id"]
    history = client.get("/api/forecast/history").json()
    assert [h["id"] for h in history] == [second["id"], first["id"]]
    assert client.get(f"/api/forecast/runs/{first['id']}").json()["trajectory"] == first["trajectory"]


def test_latest_is_404_before_any_run(client, seeded):
    assert client.get("/api/forecast/latest").status_code == 404


def test_another_users_run_is_404(client, seeded, current):
    run_id = generate(client)["id"]
    current["user_id"] = 2
    assert client.get(f"/api/forecast/runs/{run_id}").status_code == 404
    assert client.get("/api/forecast/latest").status_code == 404
    assert client.get("/api/forecast/history").json() == []


def test_other_users_data_does_not_change_my_forecast(client, db):
    seed_forecast_persona(db)
    mine = generate(client)
    seed_forecast_persona(db, user_id=2, groceries=(90000,) * 5)
    again = generate(client)
    assert again["expected_variable_expenses"] == mine["expected_variable_expenses"]


# --- recurring endpoint ---

def test_recurring_lists_salary_rent_and_sip(client, seeded):
    body = client.get("/api/forecast/recurring", params={"as_of": AS_OF}).json()
    by_kind = {item["kind"]: item for item in body["items"]}
    assert set(by_kind) == {"income", "fixed_expense", "investment"}
    assert by_kind["income"]["projected_dates"] == ["2026-11-01"]
    assert by_kind["fixed_expense"]["category"] == "Housing & Rent" and by_kind["fixed_expense"]["active"]


def test_recurring_is_empty_without_data(client, db):
    assert client.get("/api/forecast/recurring", params={"as_of": AS_OF}).json()["items"] == []
