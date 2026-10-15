# tests/test_statement_import.py
#
# The shared import pipeline driven through the CSV parser: header detection,
# amounts, dates, credits, duplicates, batches. (v2's CSV tests live on here;
# credits are now IMPORTED instead of skipped.) All data is synthetic.

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.import_batch import ImportBatch
from app.models.transaction import Transaction
from app.services.statement_import_service import get_batch, import_statement, list_batches
from app.services.statement_parsers import get_parser
from app.services.statement_parsers.tabular import assemble_rows


def run(db, text: str, user_id: int = 1, filename: str = "statement.csv") -> dict:
    return import_statement(db, user_id, filename, text.encode("utf-8"))


def counts(summary: dict) -> tuple[int, int, int]:
    return summary["rows_imported"], summary["rows_duplicate"], summary["rows_invalid"]


# ------------------------------------------------------------------ v2 behaviour, kept


def test_simple_three_column_file(db):
    summary = run(db, "description,amount,date\nSwiggy order,450.00,2026-09-15\nUber ride,820,2026-09-16\n")
    assert counts(summary) == (2, 0, 0) and summary["layout"] == "generic" and summary["source_type"] == "csv"


def test_bank_style_headers_indian_dates_and_credits_are_imported(db):
    text = "Date,Narration,Withdrawal Amt.,Deposit Amt.\n15/09/2026,UPI-SWIGGY,\"1,234.50\",\n16/09/2026,SALARY,,50000\n"
    summary = run(db, text)
    assert counts(summary) == (2, 0, 0) and summary["by_txn_type"] == {"expense": 1, "income": 1}
    rows = {t.direction: t for t in db.query(Transaction).all()}
    assert rows["debit"].amount == Decimal("1234.50") and rows["debit"].transaction_date.day == 15
    assert rows["credit"].amount == Decimal("50000.00") and rows["credit"].txn_type == "income"


def test_rupee_symbol_and_rs_prefix_are_understood(db):
    assert run(db, 'description,amount,date\nA,₹500,2026-09-01\nB,"Rs. 1,000",2026-09-02\n')["rows_imported"] == 2


def test_negative_amounts_are_imported_as_credits(db):
    run(db, "description,amount,date\nRefund from shop,-300,2026-09-01\nCoffee,150,2026-09-01\n")
    refund = db.query(Transaction).filter(Transaction.direction == "credit").one()
    assert refund.amount == Decimal("300.00") and refund.txn_type == "income"


def test_bad_rows_are_skipped_not_fatal(db):
    text = "description,amount,date\nGood,100,2026-09-01\nBad date,100,not-a-date\n,100,2026-09-01\nBad amount,abc,2026-09-01\nZero,0,2026-09-01\n"
    assert counts(run(db, text)) == (1, 0, 4)


def test_uploading_the_same_file_twice_does_not_duplicate(db):
    text = "description,amount,date\nSwiggy order,450,2026-09-15\nUber ride,820,2026-09-16\n"
    run(db, text)
    assert counts(run(db, text)) == (0, 2, 0)
    assert db.query(Transaction).count() == 2


def test_duplicates_are_checked_per_user(db):
    text = "description,amount,date\nSwiggy order,450,2026-09-15\n"
    run(db, text, user_id=1)
    assert run(db, text, user_id=2)["rows_imported"] == 1


def test_two_identical_rows_inside_one_new_file_are_both_kept(db):
    assert run(db, "description,amount,date\nCoffee,150,2026-09-15\nCoffee,150,2026-09-15\n")["rows_imported"] == 2


def test_semicolon_separated_file(db):
    assert run(db, "description;amount;date\nSwiggy;450;2026-09-15\n")["rows_imported"] == 1


def test_missing_columns_gives_a_helpful_400(db):
    with pytest.raises(HTTPException) as error:
        run(db, "name,price\nA,5\n")
    assert error.value.status_code == 400 and "description" in error.value.detail


def test_file_with_no_valid_rows_gives_400(db):
    with pytest.raises(HTTPException) as error:
        run(db, "description,amount,date\nX,abc,garbage\n")
    assert error.value.status_code == 400


def test_imported_rows_are_enriched_and_stored_as_exact_decimals(db):
    run(db, "description,amount,date\nUPI/412345678901/ZERODHA/zerodha.broking@hdfcbank,99.10,2026-09-15\n")
    tx = db.query(Transaction).one()
    assert str(tx.amount) == "99.10" and tx.direction == "debit"
    assert (tx.channel, tx.payee_key, tx.txn_type) == ("upi", "zerodha.broking@hdfcbank", "investment_out")


def test_duplicate_check_is_exact_for_paise_amounts(db):
    text = "description,amount,date\nCoffee,99.10,2026-09-15\nTea,0.30,2026-09-15\n"
    run(db, text)
    assert run(db, text)["rows_duplicate"] == 2


# ------------------------------------------------------------------ v3 (S4)


def test_preamble_lines_and_footers_are_skipped(db):
    text = (
        "Account Statement,,,\n"
        "Account holder: TEST USER,,,\n"
        ",,,\n"
        "Date,Narration,Withdrawal Amt.,Deposit Amt.\n"
        "01/09/2026,UPI-SWIGGY,450.00,\n"
        "02/09/2026,NEFT-ACME PAYROLL,,90000.00\n"
        "Page 1 of 1,,,\n"
        "Statement Summary,,,\n"
    )
    assert counts(run(db, text)) == (2, 0, 0)


def test_dateless_amountless_lines_continue_the_previous_narration(db):
    text = "Date,Narration,Debit,Credit\n01/09/2026,UPI/412345678901/SHARMA,450.00,\n,SWEETS AND NAMKEEN,,\n"
    run(db, text)
    assert db.query(Transaction).one().description == "UPI/412345678901/SHARMA SWEETS AND NAMKEEN"


def test_dr_cr_indicator_column_and_suffix(db):
    text = "Date,Description,Amount,Dr/Cr\n01/09/2026,Swiggy,450.00,DR\n02/09/2026,SB INT PD,120.00,CR\n03/09/2026,Uber,\"230.00 Dr\",\n"
    run(db, text)
    directions = {t.description: t.direction for t in db.query(Transaction).all()}
    assert directions == {"Swiggy": "debit", "SB INT PD": "credit", "Uber": "debit"}


def test_a_debit_and_a_credit_with_the_same_text_are_not_duplicates(db):
    run(db, "Date,Narration,Debit,Credit\n01/09/2026,GROWW,5000,\n")
    assert run(db, "Date,Narration,Debit,Credit\n01/09/2026,GROWW,,5000\n")["rows_imported"] == 1


def test_balance_column_is_stored(db):
    run(db, "Date,Narration,Debit,Credit,Balance\n01/09/2026,Swiggy,450.00,,\"9,550.00\"\n")
    assert db.query(Transaction).one().balance == Decimal("9550.00")


def test_batch_is_recorded_and_linked_to_its_rows(db):
    text = "description,amount,date\nSwiggy,450,2026-09-15\nBad,abc,2026-09-15\n"
    first = run(db, text)
    second = run(db, text)

    batch = get_batch(db, 1, first["import_batch_id"])
    assert (batch.rows_total, batch.rows_imported, batch.rows_invalid, batch.rows_duplicate) == (2, 1, 1, 0)
    assert [b.id for b in list_batches(db, 1)] == [second["import_batch_id"], first["import_batch_id"]]
    assert db.query(Transaction).one().import_batch_id == first["import_batch_id"]
    assert list_batches(db, 2) == []
    with pytest.raises(HTTPException) as error:
        get_batch(db, 2, first["import_batch_id"])                 # someone else's batch
    assert error.value.status_code == 404


def test_unsupported_file_type_is_400(db):
    with pytest.raises(HTTPException) as error:
        import_statement(db, 1, "notes.docx", b"hello")
    assert error.value.status_code == 400 and ".csv" in error.value.detail
    assert db.query(ImportBatch).count() == 0


def test_opening_balance_line_is_captured_not_imported():
    grid = [
        ["Txn Date", "Description", "Debit", "Credit", "Balance"],
        ["01-09-2026", "Opening Balance", "", "", "10,000.00"],
        ["02-09-2026", "Swiggy", "450.00", "", "9,550.00"],
        ["", "Closing Balance", "", "", "9,550.00"],
    ]
    result = assemble_rows(grid)
    assert result.opening_balance == Decimal("10000.00") and len(result.rows) == 1
    assert result.layout == "sbi_like" and result.has_balance is True


def test_parser_registry_picks_by_extension():
    assert get_parser("x.CSV").source_type == "csv"
