# tests/test_csv_import_service.py

import pytest
from fastapi import HTTPException

from app.models.transaction import Transaction
from app.services.csv_import_service import import_transactions_from_csv


def run(db, text: str, user_id: int = 1) -> dict:
    return import_transactions_from_csv(db, user_id, text.encode("utf-8"))


def test_simple_three_column_file(db):
    counts = run(db, "description,amount,date\nSwiggy order,450.00,2026-09-15\nUber ride,820,2026-09-16\n")
    assert counts == {"imported": 2, "skipped_invalid": 0, "skipped_duplicates": 0, "skipped_credits": 0}


def test_bank_style_headers_indian_dates_and_amount_formats(db):
    text = "Date,Narration,Withdrawal Amt.,Deposit Amt.\n15/09/2026,UPI-SWIGGY,\"1,234.50\",\n16/09/2026,SALARY,,50000\n"
    counts = run(db, text)
    assert counts["imported"] == 1 and counts["skipped_credits"] == 1
    tx = db.query(Transaction).one()
    assert tx.amount == 1234.5 and tx.transaction_date.day == 15 and tx.transaction_date.month == 9


def test_rupee_symbol_and_rs_prefix_are_understood(db):
    counts = run(db, "description,amount,date\nA,₹500,2026-09-01\nB,Rs. 1,000,2026-09-02\n".replace("Rs. 1,000", '"Rs. 1,000"'))
    assert counts["imported"] == 2


def test_negative_amounts_are_treated_as_credits(db):
    counts = run(db, "description,amount,date\nRefund,-300,2026-09-01\nCoffee,150,2026-09-01\n")
    assert counts["imported"] == 1 and counts["skipped_credits"] == 1


def test_bad_rows_are_skipped_not_fatal(db):
    text = "description,amount,date\nGood,100,2026-09-01\nBad date,100,not-a-date\n,100,2026-09-01\nBad amount,abc,2026-09-01\nZero,0,2026-09-01\n"
    counts = run(db, text)
    assert counts["imported"] == 1 and counts["skipped_invalid"] == 4


def test_uploading_the_same_file_twice_does_not_duplicate(db):
    text = "description,amount,date\nSwiggy order,450,2026-09-15\nUber ride,820,2026-09-16\n"
    run(db, text)
    second = run(db, text)
    assert second["imported"] == 0 and second["skipped_duplicates"] == 2
    assert db.query(Transaction).count() == 2


def test_duplicates_are_checked_per_user(db):
    text = "description,amount,date\nSwiggy order,450,2026-09-15\n"
    run(db, text, user_id=1)
    assert run(db, text, user_id=2)["imported"] == 1


def test_two_identical_rows_inside_one_new_file_are_both_kept(db):
    counts = run(db, "description,amount,date\nCoffee,150,2026-09-15\nCoffee,150,2026-09-15\n")
    assert counts["imported"] == 2


def test_semicolon_separated_file(db):
    assert run(db, "description;amount;date\nSwiggy;450;2026-09-15\n")["imported"] == 1


def test_missing_columns_gives_a_helpful_400(db):
    with pytest.raises(HTTPException) as error:
        run(db, "name,price\nA,5\n")
    assert error.value.status_code == 400 and "description" in error.value.detail


def test_file_with_no_valid_rows_gives_400(db):
    with pytest.raises(HTTPException) as error:
        run(db, "description,amount,date\nX,abc,garbage\n")
    assert error.value.status_code == 400
