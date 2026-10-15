# tests/test_transactions_api.py
#
# End-to-end tests of the HTTP layer (routes + services + database) using
# FastAPI's TestClient. Login is bypassed by overriding get_current_user, and
# the LLM is replaced by a fake, so these run with no Postgres and no network.

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.expense_categorizer import CategorizationResult
from app.api.deps import get_current_user
from app.api.routes import transactions as routes
from app.db.session import get_db
from app.models.user import User
from app.services import categorization_service as svc


class FakeAgent:
    def categorize_batch(self, items, examples):
        return {i.item_id: CategorizationResult("Shopping", 0.4, "guess", "llm") for i in items}


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(svc, "ExpenseCategorizerAgent", FakeAgent)
    monkeypatch.setattr(svc.time, "sleep", lambda s: None)

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 1)
    return TestClient(app)


def test_full_flow_add_upload_categorize_review_correct(client):
    r = client.post("/api/transactions/", json={"description": "Swiggy order", "amount": 450, "transaction_date": "2026-09-15T20:30:00"})
    assert r.status_code == 201 and r.json()["category"] is None and r.json()["needs_review"] is False

    csv_bytes = b"description,amount,date\nWeird merchant,999,2026-09-10\n"
    r = client.post("/api/transactions/upload-csv", files={"file": ("s.csv", csv_bytes, "text/csv")})
    assert r.status_code == 201 and r.json()["imported"] == 1

    r = client.post("/api/transactions/categorize")
    body = r.json()
    assert r.status_code == 200 and body["categorized_by_rules"] == 1 and body["categorized_by_llm"] == 1 and body["needs_review"] == 1

    review = client.get("/api/transactions/", params={"needs_review": True}).json()
    assert [t["description"] for t in review] == ["Weird merchant"]

    fixed = client.patch(f"/api/transactions/{review[0]['id']}/category", json={"category": "food & dining"})
    assert fixed.status_code == 200
    assert fixed.json()["transaction"]["category"] == "Food & Dining"
    assert fixed.json()["transaction"]["category_source"] == "user"
    assert client.get("/api/transactions/", params={"needs_review": True}).json() == []


def test_invalid_category_is_rejected_with_422(client):
    client.post("/api/transactions/", json={"description": "x", "amount": 1, "transaction_date": "2026-09-15T10:00:00"})
    r = client.patch("/api/transactions/1/category", json={"category": "Snacks"})
    assert r.status_code == 422 and "Choose one of" in r.text


def test_unknown_transaction_id_is_404(client):
    assert client.patch("/api/transactions/999/category", json={"category": "Groceries"}).status_code == 404


def test_non_csv_upload_is_rejected(client):
    r = client.post("/api/transactions/upload-csv", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_empty_categorize_run_is_a_harmless_zero(client):
    assert client.post("/api/transactions/categorize").json()["total_processed"] == 0


def test_manual_credit_entry_returns_the_new_fields(client):
    r = client.post(
        "/api/transactions/",
        json={"description": "SB INT PD SEP", "amount": 312.40, "direction": "credit", "transaction_date": "2026-09-30T00:00:00"},
    )
    body = r.json()
    assert r.status_code == 201
    assert body["direction"] == "credit" and body["txn_type"] == "income" and body["amount"] == 312.4
    assert body["user_locked"] is False and body["agent_category"] is None


def test_zero_or_negative_amount_is_rejected(client):
    for amount in (0, -50):
        r = client.post("/api/transactions/", json={"description": "x", "amount": amount, "transaction_date": "2026-09-15T10:00:00"})
        assert r.status_code == 422


def test_user_may_pick_an_income_label(client):
    client.post("/api/transactions/", json={"description": "Gift from uncle", "amount": 500, "direction": "credit", "transaction_date": "2026-09-15T10:00:00"})
    r = client.patch("/api/transactions/1/category", json={"category": "other income"})
    assert r.status_code == 200 and r.json()["transaction"]["category"] == "Other Income"
