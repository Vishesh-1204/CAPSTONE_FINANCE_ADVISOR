# tests/test_budgets_api.py
#
# HTTP-level tests for /api/profile/financial and /api/budgets/...
# Login is bypassed (user 1) and the planner uses templates only (no LLM).

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.budget_planner import BudgetPlannerAgent
from app.api.deps import get_current_user
from app.api.routes import budgets as budget_routes
from app.api.routes import profile as profile_routes
from app.db.session import get_db
from app.models.budget import Budget
from app.models.user import User
from app.services import budget_service as svc
from tests.conftest import add_expense

TARGET = "2026-10"


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(svc, "_build_planner", lambda: BudgetPlannerAgent(chains=[]))
    app = FastAPI()
    app.include_router(profile_routes.router)
    app.include_router(budget_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 1)
    return TestClient(app)


def put_profile(client, income=50000, pct=20):
    r = client.put("/api/profile/financial", json={"monthly_income": income, "savings_target_pct": pct})
    assert r.status_code == 200, r.text
    return r.json()


# --- profile ---

def test_profile_is_404_until_saved_then_round_trips(client):
    assert client.get("/api/profile/financial").status_code == 404
    saved = put_profile(client, income=60000, pct=25)
    assert saved["savings_target_amount"] == 15000.0 and saved["spendable_income"] == 45000.0
    again = put_profile(client, income=70000, pct=20)                  # update, not a second row
    assert client.get("/api/profile/financial").json()["monthly_income"] == 70000.0
    assert again["spendable_income"] == 56000.0


def test_savings_target_defaults_to_20_percent(client):
    r = client.put("/api/profile/financial", json={"monthly_income": 40000})
    assert r.status_code == 200 and r.json()["savings_target_pct"] == 20.0


@pytest.mark.parametrize("body", [
    {"monthly_income": 0},
    {"monthly_income": -5},
    {"monthly_income": 50000, "savings_target_pct": 100},
    {"monthly_income": 50000, "savings_target_pct": -1},
    {"monthly_income": 50000.123},                                     # more than 2 decimals
])
def test_invalid_profile_is_422(client, body):
    assert client.put("/api/profile/financial", json=body).status_code == 422


# --- month validation ---

@pytest.mark.parametrize("path", ["/api/budgets/generate", "/api/budgets/status", "/api/budgets/"])
@pytest.mark.parametrize("month", ["2026-13", "26-09", "2026-9", "october"])
def test_bad_month_is_422(client, path, month):
    method = client.post if path.endswith("generate") else client.get
    assert method(path, params={"month": month}).status_code == 422


def test_month_is_required(client):
    assert client.post("/api/budgets/generate").status_code == 422


# --- budgets ---

def test_generate_without_profile_is_400(client):
    r = client.post("/api/budgets/generate", params={"month": TARGET})
    assert r.status_code == 400 and "profile" in r.json()["detail"]


def test_generate_override_reset_and_list(client, db):
    put_profile(client)
    for month in ("2026-07", "2026-08", "2026-09"):
        add_expense(db, "Groceries", 6000, month=month)
        add_expense(db, "Food & Dining", 3000, month=month)

    r = client.post("/api/budgets/generate", params={"month": TARGET})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["method"] == "history" and plan["feasible"] and plan["llm_used"] is False
    assert plan["data_quality"]["months_used"] == 3
    food = next(b for b in plan["budgets"] if b["category"] == "Food & Dining")
    assert food["limit_amount"] == 2700.0 and food["source"] == "agent" and food["reasoning"]

    r = client.put(f"/api/budgets/{food['id']}", json={"limit_amount": 3500})
    assert r.status_code == 200 and r.json()["source"] == "user" and r.json()["agent_limit_amount"] == 2700.0

    regenerated = client.post("/api/budgets/generate", params={"month": TARGET}).json()
    assert next(b for b in regenerated["budgets"] if b["id"] == food["id"])["limit_amount"] == 3500.0

    r = client.post(f"/api/budgets/{food['id']}/reset")
    assert r.status_code == 200 and r.json()["limit_amount"] == 2700.0 and r.json()["source"] == "agent"

    listed = client.get("/api/budgets/", params={"month": TARGET}).json()
    assert [b["category"] for b in listed] == ["Groceries", "Food & Dining"]          # largest first
    assert client.get("/api/budgets/", params={"month": "2026-11"}).json() == []


def test_negative_override_is_422(client, db):
    put_profile(client)
    budget_id = client.post("/api/budgets/generate", params={"month": TARGET}).json()["budgets"][0]["id"]
    assert client.put(f"/api/budgets/{budget_id}", json={"limit_amount": -1}).status_code == 422


def test_other_users_budget_is_404(client, db):
    other = Budget(user_id=2, month=TARGET, category="Groceries", limit_amount=100, agent_limit_amount=100)
    db.add(other)
    db.commit()
    assert client.put(f"/api/budgets/{other.id}", json={"limit_amount": 1}).status_code == 404
    assert client.post(f"/api/budgets/{other.id}/reset").status_code == 404
    assert client.put("/api/budgets/999999", json={"limit_amount": 1}).status_code == 404
    db.refresh(other)
    assert float(other.limit_amount) == 100.0                                        # untouched


def test_status_endpoint(client, db):
    put_profile(client)
    add_expense(db, "Groceries", 6000, month="2026-09")
    client.post("/api/budgets/generate", params={"month": TARGET})
    add_expense(db, "Groceries", 6500, month=TARGET)
    add_expense(db, "Investments & Savings", 5000, month=TARGET, txn_type="investment_out")

    r = client.get("/api/budgets/status", params={"month": TARGET})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == [{
        "category": "Groceries", "limit": 6000.0, "spent": 6500.0, "remaining": -500.0,
        "percent_used": 108.3, "status": "exceeded", "source": "agent",
    }]
    assert body["invested_this_month"] == 5000.0 and body["savings_target_amount"] == 10000.0


# --- income suggestion (P5) ---

def test_income_suggestion_endpoint(client, db):
    add_expense(db, "Salary", 55000, month="2026-09", txn_type="income", direction="credit")
    r = client.get("/api/profile/financial/suggestion", params={"month": TARGET})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["suggested_monthly_income"] == 55000.0 and body["months_used"] == 1


def test_income_suggestion_month_is_optional(client):
    r = client.get("/api/profile/financial/suggestion")
    assert r.status_code == 200 and r.json()["suggested_monthly_income"] is None


@pytest.mark.parametrize("month", ["2026-13", "26-09", "october"])
def test_income_suggestion_bad_month_is_422(client, month):
    assert client.get("/api/profile/financial/suggestion", params={"month": month}).status_code == 422
