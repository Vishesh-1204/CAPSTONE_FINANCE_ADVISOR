# tests/test_budget_service.py
#
# Service-level tests for the Budget Planning Agent (SQLite + fake LLM).
# Target month is October 2026, so the default 3-month lookback is Jul-Sep 2026.

import json
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.agents.budget_planner import BudgetPlannerAgent
from app.core.categories import INVESTMENTS_CATEGORY, TRANSFERS_CATEGORY
from app.models.budget import SOURCE_AGENT, SOURCE_USER, Budget, BudgetRun
from app.services import budget_service as svc
from app.services.budget_calculator import BASELINE_WEIGHTS
from tests.conftest import add_expense

TARGET = "2026-10"
HISTORY = ("2026-07", "2026-08", "2026-09")


class FakeChain:
    """Fake LLM: returns a canned answer (or raises) and records every request."""

    def __init__(self, answer: str | Exception):
        self.answer = answer
        self.calls: list[dict] = []

    def invoke(self, inputs: dict) -> str:
        self.calls.append(inputs)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture(autouse=True)
def template_only_planner(monkeypatch):
    """No test in this file may reach a real LLM, even if the container has an API key."""
    monkeypatch.setattr(svc, "_build_planner", lambda: BudgetPlannerAgent(chains=[]))


def use_fake_llm(monkeypatch, answer: str | Exception) -> FakeChain:
    chain = FakeChain(answer)
    monkeypatch.setattr(svc, "_build_planner", lambda: BudgetPlannerAgent(chain=chain))
    return chain


def set_profile(db, income="50000", pct="20", user_id=1):
    return svc.upsert_profile(db, user_id, Decimal(income), Decimal(pct))


def monthly(db, category, amount, months=HISTORY, **kwargs):
    for month in months:
        add_expense(db, category, amount, month=month, **kwargs)


def limits(result: dict) -> dict[str, float]:
    return {b["category"]: b["limit_amount"] for b in result["budgets"]}


# --- history ---

def test_only_expenses_count_as_history(db):
    set_profile(db)
    monthly(db, "Groceries", 6000)
    monthly(db, "Food & Dining", 3000)
    add_expense(db, INVESTMENTS_CATEGORY, 10000, txn_type="investment_out")
    add_expense(db, "Salary", 50000, txn_type="income", direction="credit")
    add_expense(db, TRANSFERS_CATEGORY, 20000, txn_type="transfer")                     # own accounts
    add_expense(db, "Investment Redemption", 7000, txn_type="investment_in", direction="credit")

    result = svc.generate_budget(db, 1, TARGET)

    assert result["method"] == "history" and result["feasible"]
    assert limits(result) == {"Groceries": 6000.0, "Food & Dining": 2700.0}           # 10% trim on dining
    assert result["data_quality"]["months_used"] == 3


def test_target_month_and_months_before_the_window_are_ignored(db):
    set_profile(db)
    add_expense(db, "Groceries", 6000, month="2026-10")      # the target month itself
    add_expense(db, "Groceries", 6000, month="2026-06")      # older than the 3-month lookback
    assert svc.generate_budget(db, 1, TARGET)["method"] == "baseline"


def test_average_divides_by_months_with_any_expense(db):
    set_profile(db)
    monthly(db, "Groceries", 3000)
    add_expense(db, "Travel", 9000, month="2026-08")         # one-off trip in one of 3 months
    assert limits(svc.generate_budget(db, 1, TARGET))["Travel"] == 2700.0   # 9000/3 = 3000, trimmed 10%


def test_untyped_debits_count_as_expenses(db):
    """Pre-v3 rows have txn_type NULL; a debit is spending (same rule as /summary)."""
    set_profile(db)
    monthly(db, "Groceries", 4000, txn_type=None)
    assert limits(svc.generate_budget(db, 1, TARGET)) == {"Groceries": 4000.0}


def test_baseline_plan_when_there_is_no_history(db):
    set_profile(db)
    result = svc.generate_budget(db, 1, TARGET)
    assert result["method"] == "baseline"
    assert set(limits(result)) == set(BASELINE_WEIGHTS)
    assert sum(limits(result).values()) <= result["spendable_income"] == 40000.0


def test_infeasible_plan_reports_the_shortfall(db):
    set_profile(db, income="30000")                          # spendable 24,000
    add_expense(db, "Housing & Rent", 25000)
    add_expense(db, "Shopping", 5000)
    result = svc.generate_budget(db, 1, TARGET)
    assert not result["feasible"] and result["shortfall"] == 1000.0
    assert limits(result) == {"Housing & Rent": 25000.0, "Shopping": 0.0}
    assert "₹1,000" in result["summary"]


def test_limits_never_sum_above_spendable(db):
    set_profile(db, income="41234.57", pct="17.5")
    for category, amount in [("Housing & Rent", "15000.01"), ("Groceries", "7333.33"), ("Food & Dining", "9999.99"),
                             ("Shopping", "12345.67"), ("Entertainment", "3333.34"), (TRANSFERS_CATEGORY, "2500.5")]:
        monthly(db, category, amount)
    result = svc.generate_budget(db, 1, TARGET)
    assert result["feasible"]
    assert Decimal(str(sum(limits(result).values()))) <= Decimal(str(result["spendable_income"]))


def test_generate_without_profile_is_400(db):
    with pytest.raises(HTTPException) as exc:
        svc.generate_budget(db, 1, TARGET)
    assert exc.value.status_code == 400


def test_bad_month_is_422_in_the_service_too(db):
    set_profile(db)
    with pytest.raises(HTTPException) as exc:
        svc.generate_budget(db, 1, "2026-13")
    assert exc.value.status_code == 422


# --- data quality and research log ---

def test_data_quality_counts(db):
    set_profile(db)
    monthly(db, "Groceries", 3000)
    add_expense(db, None, 120)                                        # not categorized yet
    add_expense(db, "Shopping", 800, needs_review=True)
    add_expense(db, TRANSFERS_CATEGORY, 1500)                          # paid to a person
    add_expense(db, None, 999, month="2026-10")                        # outside the lookback
    quality = svc.generate_budget(db, 1, TARGET)["data_quality"]
    assert quality == {"months_used": 3, "uncategorized_count": 1, "needs_review_count": 1, "transfers_total": 1500.0}


def test_every_generation_is_logged_in_budget_runs(db):
    set_profile(db)
    monthly(db, "Groceries", 3000)
    svc.generate_budget(db, 1, TARGET)
    svc.generate_budget(db, 1, TARGET)
    runs = db.query(BudgetRun).filter(BudgetRun.user_id == 1).all()
    assert len(runs) == 2
    assert runs[0].method == "history" and runs[0].months_used == 3 and runs[0].feasible
    assert runs[0].llm_used is False and runs[0].explanation_faithful is None


# --- LLM behaviour through the service ---

def test_llm_down_gives_template_text(db, monkeypatch):
    set_profile(db)
    monthly(db, "Groceries", 3000)
    use_fake_llm(monkeypatch, RuntimeError("503 unavailable"))
    result = svc.generate_budget(db, 1, TARGET)
    assert not result["llm_used"] and result["summary"].startswith("Based on 3 months")
    assert db.query(BudgetRun).one().llm_used is False


def test_llm_inventing_an_amount_is_replaced_and_logged(db, monkeypatch):
    set_profile(db)
    monthly(db, "Groceries", 3000)
    use_fake_llm(monkeypatch, json.dumps({
        "summary": "You can easily spend ₹12,345 more on fun.",
        "reasons": {"Groceries": "Groceries stay at ₹3,000."},
    }))
    result = svc.generate_budget(db, 1, TARGET)
    assert result["llm_used"] and result["explanation_faithful"] is False
    assert "₹12,345" not in result["summary"]
    assert result["budgets"][0]["reasoning"] == "Groceries stay at ₹3,000."   # the faithful reason is kept
    assert db.query(BudgetRun).one().explanation_faithful is False


def test_faithful_llm_text_is_saved_as_reasoning(db, monkeypatch):
    set_profile(db)
    monthly(db, "Groceries", 3000)
    use_fake_llm(monkeypatch, json.dumps({"summary": "Groceries fit easily.", "reasons": {"Groceries": "Kept at ₹3,000."}}))
    result = svc.generate_budget(db, 1, TARGET)
    assert result["explanation_faithful"] is True and result["summary"] == "Groceries fit easily."
    assert db.query(Budget).one().reasoning == "Kept at ₹3,000."


def test_no_transaction_details_reach_the_llm(db, monkeypatch):
    """Privacy: only category names and aggregate numbers are sent."""
    set_profile(db)
    monthly(db, "Groceries", 3000, description="UPI/rajmess@okaxis/Raj Mess Hostel/4829")
    chain = use_fake_llm(monkeypatch, json.dumps({"summary": "Fine.", "reasons": {}}))
    svc.generate_budget(db, 1, TARGET)
    (request,) = chain.calls
    sent = request["facts_json"].lower()
    assert "rajmess" not in sent and "raj mess" not in sent and "okaxis" not in sent and "4829" not in sent


# --- overrides, reset, regeneration ---

def test_overrides_survive_regeneration_and_stale_agent_rows_are_deleted(db):
    set_profile(db)
    monthly(db, "Groceries", 6000)
    monthly(db, "Food & Dining", 3000)
    shopping = add_expense(db, "Shopping", 1000)
    first = svc.generate_budget(db, 1, TARGET)
    food_id = next(b["id"] for b in first["budgets"] if b["category"] == "Food & Dining")
    svc.override_budget(db, 1, food_id, Decimal("5000"))

    db.delete(shopping)                                  # history changes: Shopping disappears
    add_expense(db, "Groceries", 3000, month="2026-09", day=20)   # Sep groceries now 9000
    db.commit()
    second = svc.generate_budget(db, 1, TARGET)

    rows = {b.category: b for b in db.query(Budget).filter(Budget.user_id == 1)}
    assert set(rows) == {"Groceries", "Food & Dining"}                     # stale Shopping row deleted
    assert rows["Food & Dining"].source == SOURCE_USER and rows["Food & Dining"].limit_amount == Decimal("5000.00")
    assert rows["Food & Dining"].agent_limit_amount == Decimal("2700.00")   # the planner's number is kept
    assert rows["Groceries"].limit_amount == Decimal("7000.00")            # (6000+6000+9000)/3, refreshed
    assert rows["Groceries"].agent_limit_amount == Decimal("7000.00")
    assert len(second["budgets"]) == 2


def test_reset_restores_the_agent_limit(db):
    set_profile(db)
    monthly(db, "Food & Dining", 3000)
    budget_id = svc.generate_budget(db, 1, TARGET)["budgets"][0]["id"]
    svc.override_budget(db, 1, budget_id, Decimal("100"))
    budget = svc.reset_budget(db, 1, budget_id)
    assert budget.limit_amount == Decimal("2700.00") and budget.source == SOURCE_AGENT


# --- user isolation ---

def test_other_users_data_never_changes_my_plan(db):
    set_profile(db)
    set_profile(db, user_id=2)
    monthly(db, "Groceries", 3000)
    monthly(db, "Shopping", 99999, user_id=2)
    assert limits(svc.generate_budget(db, 1, TARGET)) == {"Groceries": 3000.0}


def test_someone_elses_budget_is_404(db):
    set_profile(db, user_id=2)
    monthly(db, "Groceries", 3000, user_id=2)
    other_id = svc.generate_budget(db, 2, TARGET)["budgets"][0]["id"]
    for action in (lambda: svc.override_budget(db, 1, other_id, Decimal("1")), lambda: svc.reset_budget(db, 1, other_id)):
        with pytest.raises(HTTPException) as exc:
            action()
        assert exc.value.status_code == 404
    assert svc.list_budgets(db, 1, TARGET) == []


# --- status ---

def test_status_compares_expenses_to_limits(db):
    set_profile(db)
    monthly(db, "Groceries", 6000)
    monthly(db, "Food & Dining", 3000)                       # -> 2700
    monthly(db, "Shopping", 1000)                            # -> 900
    svc.generate_budget(db, 1, TARGET)

    add_expense(db, "Groceries", 4900, month=TARGET)         # 81.7% -> warning
    add_expense(db, "Food & Dining", 100, month=TARGET)      # ok
    add_expense(db, "Shopping", 1000, month=TARGET)          # exceeded
    add_expense(db, "Entertainment", 300, month=TARGET)      # no limit
    add_expense(db, None, 50, month=TARGET)                  # not categorized yet
    add_expense(db, INVESTMENTS_CATEGORY, 5000, month=TARGET, txn_type="investment_out")
    add_expense(db, "Salary", 50000, month=TARGET, txn_type="income", direction="credit")

    result = svc.budget_status(db, 1, TARGET)
    by_category = {item["category"]: item for item in result["items"]}
    assert {c: i["status"] for c, i in by_category.items()} == {
        "Groceries": "warning", "Food & Dining": "ok", "Shopping": "exceeded",
        "Entertainment": "unbudgeted", "Uncategorized": "unbudgeted",
    }
    assert by_category["Groceries"]["percent_used"] == 81.7
    assert by_category["Shopping"]["remaining"] == -100.0
    assert by_category["Entertainment"]["limit"] is None
    assert result["total_limit"] == 9600.0 and result["total_spent"] == 6350.0 and result["total_remaining"] == 3250.0
    assert result["invested_this_month"] == 5000.0               # saved, not spent
    assert result["savings_target_amount"] == 10000.0


def test_zero_limit_with_spending_is_exceeded(db):
    set_profile(db)
    monthly(db, "Shopping", 1000)
    budget_id = svc.generate_budget(db, 1, TARGET)["budgets"][0]["id"]
    svc.override_budget(db, 1, budget_id, Decimal("0"))
    add_expense(db, "Shopping", 10, month=TARGET)
    (item,) = svc.budget_status(db, 1, TARGET)["items"]
    assert item["status"] == "exceeded" and item["percent_used"] is None


def test_status_without_budgets_or_profile(db):
    add_expense(db, "Groceries", 500, month=TARGET)
    result = svc.budget_status(db, 1, TARGET)
    assert result["items"][0]["status"] == "unbudgeted"
    assert result["total_limit"] == 0.0 and result["savings_target_amount"] is None


# --- income suggestion (P5) ---

def credit(db, amount, month, category="Salary", txn_type="income", user_id=1):
    """One money-in row (salary by default)."""
    return add_expense(db, category, amount, month=month, txn_type=txn_type, direction="credit", user_id=user_id)


def test_income_suggestion_averages_income_credits(db):
    credit(db, 60000, "2026-07")
    credit(db, 60000, "2026-08")
    credit(db, 60000, "2026-09")
    credit(db, 900, "2026-09", category="Interest")
    result = svc.suggest_income(db, 1, TARGET)
    assert result["suggested_monthly_income"] == 60300.0          # (180000 + 900) / 3
    assert result["months_used"] == 3
    assert (result["window_start"], result["window_end"]) == ("2026-07", "2026-09")
    assert result["income_by_month"][-1] == {"month": "2026-09", "total": 60900.0}


def test_month_with_data_but_no_income_counts_as_zero(db):
    credit(db, 90000, "2026-07")
    add_expense(db, "Groceries", 500, month="2026-08")            # statement imported, no salary
    credit(db, 30000, "2026-09")
    result = svc.suggest_income(db, 1, TARGET)
    assert result["months_used"] == 3
    assert result["suggested_monthly_income"] == 40000.0           # 120000 / 3, not / 2


def test_month_never_imported_does_not_lower_the_average(db):
    credit(db, 50000, "2026-09")                                   # only September was imported
    result = svc.suggest_income(db, 1, TARGET)
    assert result["months_used"] == 1 and result["suggested_monthly_income"] == 50000.0


def test_refunds_redemptions_transfers_and_untyped_credits_are_not_income(db):
    credit(db, 50000, "2026-09")
    credit(db, 2000, "2026-09", category="Refund & Cashback")
    credit(db, 8000, "2026-09", category="Investment Redemption", txn_type="investment_in")
    credit(db, 15000, "2026-09", category=TRANSFERS_CATEGORY, txn_type="transfer")
    credit(db, 7000, "2026-09", category=None, txn_type=None)      # pre-v3 row: unknown kind of credit
    assert svc.suggest_income(db, 1, TARGET)["suggested_monthly_income"] == 50000.0


def test_target_month_and_months_before_the_window_are_not_used(db):
    credit(db, 99999, "2026-06")                                   # before the Jul-Sep window
    credit(db, 99999, TARGET)                                      # the month itself
    credit(db, 40000, "2026-08")
    result = svc.suggest_income(db, 1, TARGET)
    assert result["months_used"] == 1 and result["suggested_monthly_income"] == 40000.0


def test_no_income_gives_none_and_a_message(db):
    add_expense(db, "Groceries", 500, month="2026-09")
    result = svc.suggest_income(db, 1, TARGET)
    assert result["suggested_monthly_income"] is None
    assert "No income" in result["message"]


def test_other_users_income_is_invisible(db):
    credit(db, 80000, "2026-09", user_id=2)
    result = svc.suggest_income(db, 1, TARGET)
    assert result["suggested_monthly_income"] is None and result["months_used"] == 0


def test_suggestion_never_saves_the_profile(db):
    credit(db, 50000, "2026-09")
    svc.suggest_income(db, 1, TARGET)
    assert svc.get_profile(db, 1) is None
