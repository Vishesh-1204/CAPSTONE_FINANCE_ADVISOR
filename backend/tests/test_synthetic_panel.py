# tests/test_synthetic_panel.py
#
# The 24-month forecast panel must be reproducible (same seed -> same rows),
# use only real labels, keep a correct running balance, and contain the effects
# the evaluation relies on: fixed recurring items, a salary hike, festival
# spikes, one medical bill per user, and some real dips below zero.

import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

import pytest

from app.core.categories import ALL_CATEGORY_NAMES, SALARY_CATEGORY
from app.core.transaction_types import CREDIT, DEBIT, TXN_TYPES
from scripts.make_synthetic_panel import (
    FIRST_MONTH, HIKE_MONTH, N_MONTHS, PERSONAS, generate_panel, panel_months, panel_sha256, read_csv, user_ids,
    write_csv,
)


@pytest.fixture(scope="module")
def rows():
    return generate_panel()


def test_same_seed_gives_identical_rows_and_a_different_seed_does_not(rows):
    assert generate_panel(42) == rows
    assert panel_sha256(generate_panel(42)) == panel_sha256(rows)
    assert generate_panel(7) != rows


def test_a_user_subset_has_exactly_that_users_rows(rows):
    # each user has its own random generator, so adding users never changes the others
    assert generate_panel(users=["u02", "u17"]) == [r for r in rows if r.user in {"u02", "u17"}]


def test_csv_round_trip_is_lossless(rows, tmp_path):
    write_csv(rows, tmp_path / "panel.csv")
    assert read_csv(tmp_path / "panel.csv") == rows


def test_thirty_users_four_personas_twenty_four_months(rows):
    assert len(user_ids()) == 30 and {p.name for p in PERSONAS} == {r.persona for r in rows}
    expected_months = {f"{m:%Y-%m}" for m in panel_months()}
    assert len(expected_months) == N_MONTHS and min(r.day for r in rows) >= FIRST_MONTH
    for user, _ in user_ids():
        assert {f"{r.day:%Y-%m}" for r in rows if r.user == user} == expected_months


def test_every_label_is_real_and_every_amount_positive(rows):
    assert {r.category for r in rows} <= set(ALL_CATEGORY_NAMES)
    assert {r.txn_type for r in rows} <= set(TXN_TYPES)
    assert {r.direction for r in rows} <= {DEBIT, CREDIT}
    assert all(r.amount > 0 for r in rows)


def test_balance_column_is_the_running_balance(rows):
    by_user = defaultdict(list)
    for r in rows:
        by_user[r.user].append(r)
    for user_rows in by_user.values():
        assert [r.day for r in user_rows] == sorted(r.day for r in user_rows)      # statement order
        for previous, row in zip(user_rows, user_rows[1:]):
            change = row.amount if row.direction == CREDIT else -row.amount
            assert row.balance == previous.balance + change


def test_recurring_rows_have_a_fixed_day_and_amount(rows):
    days, amounts = defaultdict(set), defaultdict(set)
    for r in rows:
        if r.recurring:
            days[(r.user, r.description)].add(r.day.day)
            amounts[(r.user, r.description)].add(r.amount)
    assert days and all(len(d) == 1 for d in days.values())
    for (user, description), values in amounts.items():
        assert len(values) == (2 if "PAYROLL" in description else 1), (user, description)   # salary: before/after hike


def test_every_salaried_user_gets_one_hike_in_april_2026(rows):
    salaries = defaultdict(dict)
    for r in rows:
        if r.category == SALARY_CATEGORY:
            salaries[r.user][r.day.replace(day=1)] = r.amount
    assert len(salaries) == sum(p.users for p in PERSONAS if p.salaried)
    for by_month in salaries.values():
        before, after = by_month[date(2026, 3, 1)], by_month[HIKE_MONTH]
        assert after > before * Decimal("1.07")


def test_one_medical_bill_per_user(rows):
    bills = Counter(r.user for r in rows if r.description == "SYNTH HOSPITAL BILL")
    assert set(bills.values()) == {1} and len(bills) == 30


def test_festival_months_spend_more_on_shopping(rows):
    monthly = defaultdict(Decimal)
    for r in rows:
        if r.category == "Shopping":
            monthly[r.day.replace(day=1)] += r.amount
    festival = [v for m, v in monthly.items() if m.month in (10, 11)]
    other = [v for m, v in monthly.items() if m.month not in (10, 11)]
    assert min(festival) > Decimal("1.4") * (sum(other) / len(other))


def test_cash_crunch_events_exist_only_for_the_tight_personas(rows):
    dipped = {r.persona for r in rows if r.balance < 0}
    assert {"high_spender", "freelancer"} <= dipped
    assert not dipped & {"steady", "saver"}


def test_person_to_person_payees_are_ids_not_names(rows):
    transfers = [r.description for r in rows if r.category == "Transfers" and r.txn_type == "expense"]
    assert transfers and all(re.fullmatch(r"UPI/P2P/PAYEE-\d{4}", d) for d in transfers)
