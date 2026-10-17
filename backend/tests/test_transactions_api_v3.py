# tests/test_transactions_api_v3.py
#
# HTTP-level tests for the S3 endpoints: list filters + paging envelope,
# monthly summary, bulk, unlock, extended PATCH and payee rules.
# Login is bypassed (user 1) and the LLM is a fake.

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.expense_categorizer import CategorizationResult
from app.api.deps import get_current_user
from app.api.routes import payee_rules as rule_routes
from app.api.routes import transactions as tx_routes
from app.db.session import get_db
from app.models.user import User
from app.services import categorization_service as svc


class FakeAgent:
    def categorize_batch(self, items, examples):
        return {i.item_id: CategorizationResult("Shopping", 0.4, "unsure", "llm") for i in items}


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setattr(svc, "ExpenseCategorizerAgent", FakeAgent)
    monkeypatch.setattr(svc.time, "sleep", lambda s: None)
    app = FastAPI()
    app.include_router(tx_routes.router)
    app.include_router(rule_routes.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 1)
    return TestClient(app)


def add(client, description, amount, day, direction="debit", month=9) -> int:
    r = client.post("/api/transactions/", json={
        "description": description, "amount": amount, "direction": direction,
        "transaction_date": f"2026-{month:02d}-{day:02d}T10:00:00",
    })
    assert r.status_code == 201, r.text
    return r.json()["id"]


def listing(client, **params) -> dict:
    r = client.get("/api/transactions/", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_status_filters_partition_the_rows(client):
    swiggy = add(client, "Swiggy dinner", 450, 1)          # rule -> categorized
    add(client, "Weird merchant", 999, 2)                  # fake LLM 0.4 -> needs_review
    locked = add(client, "Odd store", 120, 3)
    client.post("/api/transactions/categorize")
    client.patch(f"/api/transactions/{locked}/category", json={"category": "Groceries"})
    add(client, "Brand new", 50, 4)                        # never categorized

    counts = {s: listing(client, status=s)["total"] for s in ("needs_review", "uncategorized", "categorized", "locked")}
    assert counts == {"needs_review": 1, "uncategorized": 1, "categorized": 1, "locked": 1}
    assert listing(client, status="categorized")["items"][0]["id"] == swiggy
    assert listing(client)["total"] == 4


def test_type_category_date_filters_and_paging(client):
    for day in range(1, 8):
        add(client, f"Swiggy order {chr(96 + day)}", 100 + day, day)
    add(client, "NACH/HDFC MF SIP", 5000, 10)
    client.post("/api/transactions/categorize")

    assert listing(client, txn_type="investment_out")["total"] == 1
    assert listing(client, category="food & dining")["total"] == 7
    assert listing(client, date_from="2026-09-03", date_to="2026-09-05")["total"] == 3     # inclusive

    page = listing(client, limit=3, offset=3)
    assert page["total"] == 8 and len(page["items"]) == 3 and page["offset"] == 3


def test_bad_filter_values_are_422(client):
    assert client.get("/api/transactions/", params={"status": "weird"}).status_code == 422
    assert client.get("/api/transactions/", params={"category": "Snacks"}).status_code == 422
    assert client.get("/api/transactions/", params={"limit": 0}).status_code == 422


def test_summary_excludes_investments_redemptions_and_self_transfers(client):
    add(client, "SALARY CREDIT SEP", 100000, 1, "credit")
    add(client, "Swiggy dinner", 500, 2)
    add(client, "Weird merchant", 1500, 3)                 # categorized later by the fake LLM
    add(client, "NACH/HDFC MF SIP", 10000, 5)
    add(client, "ZERODHA BROKING WITHDRAWAL", 3000, 6, "credit")
    add(client, "IMPS to own account HDFC", 20000, 7)
    add(client, "Swiggy dinner", 999, 1, month=10)         # other month: excluded
    client.post("/api/transactions/categorize")

    r = client.get("/api/transactions/summary", params={"month": "2026-09"})
    body = r.json()
    assert r.status_code == 200 and body["month"] == "2026-09" and body["transaction_count"] == 6
    assert body["expense_total"] == 2000.0
    assert body["income_total"] == 100000.0
    assert body["investment_out_total"] == 10000.0
    assert body["redemption_total"] == 3000.0
    assert body["transfer_total"] == 20000.0
    assert body["net_investment_flow"] == 7000.0
    assert body["savings_investment_rate"] == 0.98         # (100000 - 2000) / 100000
    assert body["by_category"] == [
        {"category": "Shopping", "total": 1500.0, "count": 1},
        {"category": "Food & Dining", "total": 500.0, "count": 1},
    ]


def test_summary_without_income_has_no_rate_and_bad_month_is_422(client):
    add(client, "Swiggy dinner", 500, 2)
    assert client.get("/api/transactions/summary", params={"month": "2026-09"}).json()["savings_investment_rate"] is None
    assert client.get("/api/transactions/summary", params={"month": "Sept"}).status_code == 422


def test_patch_remember_payee_then_list_and_delete_rules(client):
    tx = add(client, "UPI/412345678901/RAJ MESS/rajmess@okaxis", 3000, 1)
    r = client.patch(f"/api/transactions/{tx}/category", json={"category": "Food & Dining", "remember_payee": True})
    body = r.json()
    assert r.status_code == 200 and body["similar_updated"] == 0
    assert body["transaction"]["user_locked"] is True
    assert body["payee_rule"]["match_type"] == "upi_id" and body["payee_rule"]["match_value"] == "rajmess@okaxis"

    rules = client.get("/api/payee-rules/").json()
    assert [rule["id"] for rule in rules] == [body["payee_rule"]["id"]]
    assert client.delete(f"/api/payee-rules/{rules[0]['id']}").status_code == 204
    assert client.get("/api/payee-rules/").json() == []
    assert client.delete(f"/api/payee-rules/{rules[0]['id']}").status_code == 404


def test_patch_rejects_a_label_that_does_not_fit_the_direction(client):
    tx = add(client, "Swiggy dinner", 450, 1)
    r = client.patch(f"/api/transactions/{tx}/category", json={"category": "Salary"})
    assert r.status_code == 422 and "debit" in r.text


def test_bulk_and_unlock_endpoints(client):
    a, b = add(client, "Odd store", 10, 1), add(client, "Weird shop", 20, 2)
    r = client.post("/api/transactions/bulk-category", json={"transaction_ids": [a, b, 999], "category": "Groceries"})
    assert r.status_code == 200 and r.json()["updated"] == 2 and r.json()["not_found_ids"] == [999]
    assert listing(client, status="locked")["total"] == 2

    r = client.post(f"/api/transactions/{a}/unlock")
    assert r.status_code == 200 and r.json()["user_locked"] is False and r.json()["category"] == "Groceries"
    assert client.post("/api/transactions/999/unlock").status_code == 404
    assert client.post("/api/transactions/bulk-category", json={"transaction_ids": [], "category": "Groceries"}).status_code == 422
