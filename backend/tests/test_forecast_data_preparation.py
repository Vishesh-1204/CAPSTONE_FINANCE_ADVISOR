# tests/test_forecast_data_preparation.py
#
# The forecast's database step: history span, recurring detection per kind,
# the variable series (rent not counted twice), opening balance precedence and
# the income source. SQLite in memory (conftest `db`), synthetic rows only.

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.financial_profile import FinancialProfile
from app.models.transaction import Transaction
from app.services.forecasting.data_preparation import (
    KIND_FIXED_EXPENSE, KIND_INCOME, KIND_INVESTMENT, history_span, prepare_history, recurrence_key,
    resolve_income, resolve_opening_balance,
)

AS_OF = date(2026, 10, 22)
HISTORY = ("2026-06", "2026-07", "2026-08", "2026-09")
MONTH_NAMES = {"2026-06": "JUN", "2026-07": "JUL", "2026-08": "AUG", "2026-09": "SEP", "2026-10": "OCT"}


def _tx(db, on: date, amount, *, direction="debit", txn_type="expense", category=None,
        description="POS SYNTH SHOP", balance=None, user_id=1, payee_key=None) -> Transaction:
    tx = Transaction(
        user_id=user_id, description=description, amount=Decimal(str(amount)), direction=direction,
        txn_type=txn_type, category=category, payee_key=payee_key,
        balance=Decimal(str(balance)) if balance is not None else None,
        transaction_date=datetime(on.year, on.month, on.day, 12, tzinfo=timezone.utc),
    )
    db.add(tx)
    db.commit()
    return tx


def _day(month: str, day: int) -> date:
    year, mon = (int(part) for part in month.split("-"))
    return date(year, mon, day)


def _seed_persona(db, user_id: int = 1, groceries=(5000, 6000, 5500, 6500)) -> None:
    """June-September history plus the first days of October (the current, partial month)."""
    for index, month in enumerate(HISTORY + ("2026-10",)):
        _tx(db, _day(month, 1), 60000, direction="credit", txn_type="income", category="Salary",
            description=f"NEFT-ACME CORP SALARY {MONTH_NAMES[month]} 2026", user_id=user_id)
        _tx(db, _day(month, 5), 18000, category="Housing & Rent", user_id=user_id,
            description=f"UPI/41234567{index}/LANDLORD/landlord.r@okaxis")
        _tx(db, _day(month, 10), 3000, txn_type="investment_out", category="Investments & Savings",
            description="NACH/SYNTH MF SIP/FOLIO 0000001", user_id=user_id)
        grocery = groceries[index] if index < len(groceries) else 2000
        _tx(db, _day(month, 12), grocery, category="Groceries", description="POS SYNTH KIRANA STORE",
            user_id=user_id, balance=100000 + index)
    # Money movements that are neither income nor spending
    _tx(db, date(2026, 7, 18), 1200, direction="credit", txn_type="income", category="Refund & Cashback",
        description="REFUND SYNTH ONLINE MARKETPLACE", user_id=user_id)
    _tx(db, date(2026, 8, 20), 10000, txn_type="transfer", category="Transfers",
        description="IMPS/OWN ACCOUNT TRANSFER SELF", user_id=user_id)
    _tx(db, date(2026, 7, 22), 6000, direction="credit", txn_type="investment_in",
        category="Investment Redemption", description="SYNTH MF REDEMPTION PAYOUT", user_id=user_id)


@pytest.fixture()
def seeded(db):
    """Tests that take `seeded` start with the standard persona; tests with only `db` start empty."""
    _seed_persona(db)
    return db


# --- recurrence_key ---

def test_recurrence_key_drops_month_names():
    assert recurrence_key("acme corp salary oct", "x") == recurrence_key("acme corp salary nov", "x") \
        == "acme corp salary"


def test_recurrence_key_keeps_upi_ids_and_falls_back_to_the_narration():
    assert recurrence_key("landlord.r@okaxis", "x") == "landlord.r@okaxis"
    assert recurrence_key(None, "NEFT-ACME CORP SALARY DEC 2026") == "acme corp salary"


# --- history_span (pure) ---

def _keys(months: list[date]) -> list[str]:
    return [f"{m.year:04d}-{m.month:02d}" for m in months]


def test_span_ends_at_the_last_complete_month_before_as_of():
    assert _keys(history_span(date(2026, 6, 3), date(2026, 10, 20), AS_OF)) == list(HISTORY)


def test_as_of_on_a_month_end_includes_that_month():
    assert _keys(history_span(date(2026, 9, 1), date(2026, 10, 31), date(2026, 10, 31))) == ["2026-09", "2026-10"]


def test_lagging_statements_do_not_create_fake_zero_months():
    # Last import ends 28 September, forecast made on 22 November: October is NOT a 0 month
    assert _keys(history_span(date(2026, 7, 1), date(2026, 9, 28), date(2026, 11, 22))) == [
        "2026-07", "2026-08", "2026-09"]


def test_only_the_current_month_gives_an_empty_span():
    assert history_span(date(2026, 10, 2), date(2026, 10, 20), AS_OF) == []


# --- prepare_history ---

def test_recurring_items_are_detected_per_kind(db, seeded):
    history = prepare_history(db, 1, AS_OF)
    by_kind = {r.kind: r for r in history.recurring}
    assert set(by_kind) == {KIND_INCOME, KIND_FIXED_EXPENSE, KIND_INVESTMENT}
    salary = by_kind[KIND_INCOME]
    assert salary.item.payee_key == "acme corp salary" and salary.item.occurrences == 5   # JUN..OCT grouped
    assert salary.category == "Salary" and salary.active
    rent = by_kind[KIND_FIXED_EXPENSE]
    assert rent.item.typical_amount == 18000 and rent.item.typical_day == 5 and rent.category == "Housing & Rent"
    assert by_kind[KIND_INVESTMENT].item.typical_amount == 3000


def test_rent_is_not_counted_twice_in_the_variable_series(db, seeded):
    history = prepare_history(db, 1, AS_OF)
    assert history.months == list(HISTORY)
    assert history.variable_series == [5000, 6000, 5500, 6500]                 # groceries only
    assert history.total_expense_series == [23000, 24000, 23500, 24500]       # + rent (ablation series)
    assert history.first_forecast_month == date(2026, 10, 1)
    assert history.data_end == date(2026, 10, 12)


def test_sip_refund_transfer_and_redemption_are_not_spending(db, seeded):
    history = prepare_history(db, 1, AS_OF)
    # Only groceries and rent are expenses; the 10,000 self-transfer in August is not
    assert history.total_expense_series[2] == 23500


def test_a_one_off_payment_to_the_landlord_stays_variable(db, seeded):
    _tx(db, date(2026, 8, 25), 50000, category="Housing & Rent", description="UPI/999/LANDLORD/landlord.r@okaxis")
    history = prepare_history(db, 1, AS_OF)
    assert history.variable_series[2] == 5500 + 50000


def test_empty_months_inside_the_history_count_as_zero(db):
    _tx(db, date(2026, 7, 10), 4000, category="Groceries")
    _tx(db, date(2026, 9, 10), 6000, category="Groceries")
    history = prepare_history(db, 1, AS_OF)
    assert history.months == ["2026-07", "2026-08", "2026-09"]
    assert history.variable_series == [4000, 0, 6000]


def test_less_than_one_complete_month_is_422(db):
    _tx(db, date(2026, 10, 3), 500, category="Groceries")           # current, partial month only
    with pytest.raises(HTTPException) as error:
        prepare_history(db, 1, AS_OF)
    assert error.value.status_code == 422


def test_no_transactions_at_all_is_422(db):
    with pytest.raises(HTTPException) as error:
        prepare_history(db, 1, AS_OF)
    assert error.value.status_code == 422


def test_rows_after_as_of_are_ignored(db, seeded):
    _tx(db, date(2026, 10, 25), 99999, category="Shopping")
    early = prepare_history(db, 1, date(2026, 9, 15))                # clock moved back to mid-September
    assert early.months == ["2026-06", "2026-07", "2026-08"]
    assert early.data_end == date(2026, 9, 12)


def test_other_users_rows_never_leak_in(db, seeded):
    _seed_persona(db, user_id=2, groceries=(90000, 90000, 90000, 90000))
    assert prepare_history(db, 1, AS_OF).variable_series == [5000, 6000, 5500, 6500]


def test_a_stopped_subscription_is_shown_but_inactive(db):
    for month in ("2026-04", "2026-05", "2026-06"):
        _tx(db, _day(month, 10), 199, category="Subscriptions", description="SYNTH STREAMING MONTHLY")
    _tx(db, date(2026, 9, 28), 3000, category="Groceries")           # data continues until late September
    item = next(r for r in prepare_history(db, 1, AS_OF).recurring if r.kind == KIND_FIXED_EXPENSE)
    assert item.item.payee_key == "synth streaming monthly" and not item.active


# --- opening balance ---

def test_requested_opening_balance_wins(db, seeded):
    opening = resolve_opening_balance(db, 1, AS_OF, Decimal("2500.50"))
    assert (opening.amount, opening.source, opening.on) == (Decimal("2500.50"), "request", None)


def test_latest_statement_balance_is_used_otherwise(db, seeded):
    opening = resolve_opening_balance(db, 1, AS_OF, None)
    assert (opening.amount, opening.source, opening.on) == (Decimal("100004.00"), "statement", date(2026, 10, 12))


def test_statement_balance_after_as_of_is_ignored(db, seeded):
    opening = resolve_opening_balance(db, 1, date(2026, 9, 15), None)
    assert opening.amount == Decimal("100003.00") and opening.on == date(2026, 9, 12)


def test_no_balance_anywhere_is_400(db):
    _tx(db, date(2026, 9, 10), 500, category="Groceries")
    with pytest.raises(HTTPException) as error:
        resolve_opening_balance(db, 1, AS_OF, None)
    assert error.value.status_code == 400


def test_other_users_balance_is_never_used(db):
    _tx(db, date(2026, 9, 10), 500, category="Groceries", balance=777, user_id=2)
    with pytest.raises(HTTPException):
        resolve_opening_balance(db, 1, AS_OF, None)


# --- income source ---

def test_recurring_salary_is_the_income_source(db, seeded):
    income = resolve_income(db, 1, prepare_history(db, 1, AS_OF).recurring)
    assert (income.source, income.monthly_amount) == ("recurring", Decimal("60000.00"))


def test_profile_income_is_used_when_no_salary_is_detected(db):
    _tx(db, date(2026, 9, 10), 500, category="Groceries")
    db.add(FinancialProfile(user_id=1, monthly_income=Decimal("45000")))
    db.commit()
    income = resolve_income(db, 1, prepare_history(db, 1, AS_OF).recurring)
    assert (income.source, income.monthly_amount) == ("profile", Decimal("45000.00"))


def test_no_salary_and_no_profile_means_income_none(db):
    _tx(db, date(2026, 9, 10), 500, category="Groceries")
    income = resolve_income(db, 1, prepare_history(db, 1, AS_OF).recurring)
    assert (income.source, income.monthly_amount) == ("none", Decimal("0.00"))
