# tests/test_statement_parsers.py
#
# S5: XLSX + PDF parsing, passwords, scanned PDFs, balance check and
# cross-format duplicates, using the SYNTHETIC fixtures in
# tests/fixtures/statements/ (made by scripts/make_synthetic_statements.py).
# The tests only READ the committed fixtures, so they never need reportlab.

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.models.transaction import Transaction
from app.services.statement_import_service import check_balances, import_statement
from app.services.statement_parsers import ParsedRow, StatementParseError, get_parser
from app.services.statement_parsers.tabular import join_narration

FIXTURES = Path(__file__).parent / "fixtures" / "statements"
TRUTH = json.loads((FIXTURES / "ground_truth.json").read_text(encoding="utf-8"))
PASSWORD = TRUTH["files"]["protected.pdf"]["password"]
READABLE = [name for name, spec in TRUTH["files"].items() if "expect_error" not in spec]


def parse(name: str, password: str | None = None):
    return get_parser(name).parse((FIXTURES / name).read_bytes(), password=password)


def upload(db, name: str, password: str | None = None, user_id: int = 1) -> dict:
    return import_statement(db, user_id, name, (FIXTURES / name).read_bytes(), password=password)


# ------------------------------------------------------------------ every readable fixture


@pytest.mark.parametrize("name", READABLE)
def test_each_fixture_parses_to_the_ground_truth(name):
    spec = TRUTH["files"][name]
    result = parse(name, spec.get("password"))

    assert (len(result.rows), result.invalid_rows, result.layout) == (spec["rows"], spec["invalid"], spec["layout"])
    assert result.has_balance is True
    for row, truth in zip(result.rows, TRUTH["transactions"]):
        assert row.date.date().isoformat() == truth["date"]
        assert row.direction == truth["direction"]
        # sbi_like.pdf prints ONE amount wrongly on purpose (the planted balance error)
        if not (name == "sbi_like.pdf" and row.date.day == spec.get("mismatch_day")):
            assert row.amount == Decimal(truth["amount"])


def test_hdfc_pdf_really_has_two_pages_with_a_repeated_header():
    import pdfplumber
    with pdfplumber.open(FIXTURES / "hdfc_like.pdf") as pdf:
        assert len(pdf.pages) == 2
        assert "Narration" in (pdf.pages[1].extract_text() or "")    # header printed again on page 2
    assert len(parse("hdfc_like.pdf").rows) == 24                      # ...but not imported as a row


def test_unruled_pdf_joins_wrapped_narrations_and_reads_the_opening_balance():
    result = parse("sbi_like.pdf")
    narrations = [row.narration for row in result.rows]
    assert "UPI/412345678902/RAHUL KUMAR/9876543210@ybl" in narrations     # word wrap -> space
    assert "UPI/412345678901/SWIGGY/swiggy@icici" in narrations            # cut at '@' -> glued
    assert result.opening_balance == Decimal("25000.00")


@pytest.mark.parametrize("previous, continuation, joined", [
    ("UPI/4123/SWIGGY/swiggy", "@icici", "UPI/4123/SWIGGY/swiggy@icici"),
    ("NEFT-ACME/", "PAYROLL", "NEFT-ACME/PAYROLL"),
    ("MONTHLY HOUSE", "RENT SEP", "MONTHLY HOUSE RENT SEP"),
])
def test_join_narration(previous, continuation, joined):
    assert join_narration(previous, continuation) == joined


def test_xlsx_reads_real_date_and_number_cells():
    first = parse("statement.xlsx").rows[0]
    assert first.date == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert first.debit == Decimal("450.00") and first.balance == Decimal("24550.00")


# ------------------------------------------------------------------ errors


def test_scanned_pdf_is_a_clear_422(db):
    with pytest.raises(HTTPException) as error:
        upload(db, "scanned.pdf")
    assert error.value.status_code == 422
    assert error.value.detail == "This looks like a scanned PDF. Scanned statements are not supported yet."


@pytest.mark.parametrize("password, message", [
    (None, "password-protected"),
    ("wrong-pass-xyz", "password is incorrect"),
])
def test_protected_pdf_needs_the_right_password(password, message):
    with pytest.raises(StatementParseError) as error:
        parse("protected.pdf", password)
    assert error.value.status_code == 400 and message in error.value.message


def test_password_never_appears_in_logs_or_errors(db, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(HTTPException) as error:
        upload(db, "protected.pdf", password="wrong-pass-xyz")
    assert "wrong-pass-xyz" not in error.value.detail
    upload(db, "protected.pdf", password=PASSWORD)
    assert "wrong-pass-xyz" not in caplog.text and PASSWORD not in caplog.text


@pytest.mark.parametrize("filename", ["fake.pdf", "fake.xlsx"])
def test_garbage_files_are_a_400_not_a_crash(filename):
    with pytest.raises(StatementParseError) as error:
        get_parser(filename).parse(b"this is not really a document")
    assert error.value.status_code == 400


# ------------------------------------------------------------------ balance check


def _row(n: int, day: int, debit: str | None, credit: str | None, balance: str | None) -> ParsedRow:
    return ParsedRow(
        row_number=n, date=datetime(2026, 9, day, tzinfo=timezone.utc), narration=f"row {n}",
        debit=Decimal(debit) if debit else None, credit=Decimal(credit) if credit else None,
        balance=Decimal(balance) if balance else None,
    )


def test_balance_check_passes_and_marks_rows():
    rows = [_row(1, 1, "100", None, "900"), _row(2, 2, None, "50", "950")]
    assert check_balances(rows, opening_balance=Decimal("1000")) == []
    assert [r.balance_ok for r in rows] == [True, True]


def test_balance_check_without_opening_balance_cannot_check_row_one():
    rows = [_row(1, 1, "100", None, "900"), _row(2, 2, None, "50", "950")]
    check_balances(rows)
    assert [r.balance_ok for r in rows] == [None, True]


def test_one_bad_row_gives_exactly_one_mismatch():
    rows = [_row(1, 1, "100", None, "900"), _row(2, 2, "60", None, "850"), _row(3, 3, "50", None, "800")]
    assert check_balances(rows, Decimal("1000")) == [2]              # 900 - 60 = 840, not 850
    assert [r.balance_ok for r in rows] == [True, False, True]       # chain restarts from 850


def test_newest_first_statements_are_checked_in_date_order():
    rows = [_row(1, 3, None, "50", "950"), _row(2, 1, "100", None, "900")]
    assert check_balances(rows, Decimal("1000")) == []


def test_a_missing_balance_breaks_the_chain_without_false_alarms():
    rows = [_row(1, 1, "100", None, "900"), _row(2, 2, "60", None, None), _row(3, 3, "50", None, "790")]
    assert check_balances(rows, Decimal("1000")) == []
    assert rows[2].balance_ok is None


def test_planted_balance_error_is_found_and_stored(db):
    summary = upload(db, "sbi_like.pdf")
    assert summary["balance_checked"] is True and summary["balance_mismatches"] == 1
    assert len(summary["balance_mismatch_rows"]) == 1

    stored = db.query(Transaction).order_by(Transaction.transaction_date).all()
    wrong = [t for t in stored if t.balance_ok is False]
    assert len(wrong) == 1 and wrong[0].transaction_date.day == TRUTH["files"]["sbi_like.pdf"]["mismatch_day"]
    assert all(t.balance_ok is True for t in stored if t is not wrong[0])     # opening balance -> row 1 checked too


def test_clean_statement_has_no_mismatches(db):
    summary = upload(db, "hdfc_like.pdf")
    assert (summary["balance_checked"], summary["balance_mismatches"], summary["rows_imported"]) == (True, 0, 24)
    assert summary["layout"] == "hdfc_like" and summary["source_type"] == "pdf"


# ------------------------------------------------------------------ cross-format duplicates


def test_same_statement_in_three_formats_is_imported_once(db):
    first = upload(db, "messy.csv")
    from_pdf = upload(db, "sbi_like.pdf")          # PDF narrations differ by inner spaces only
    from_xlsx = upload(db, "statement.xlsx")

    assert first["rows_imported"] == 24
    # sbi_like prints one amount wrongly (planted error), so exactly that row looks new
    assert (from_pdf["rows_imported"], from_pdf["rows_duplicate"]) == (1, 23)
    assert (from_xlsx["rows_imported"], from_xlsx["rows_duplicate"]) == (0, 24)


def test_credits_and_investments_from_a_pdf_are_typed(db):
    summary = upload(db, "hdfc_like.pdf")
    expected = {}
    for truth in TRUTH["transactions"]:
        if not truth["is_person"]:
            expected[truth["txn_type"]] = expected.get(truth["txn_type"], 0) + 1
    for txn_type in ("income", "investment_out", "investment_in", "transfer"):
        assert summary["by_txn_type"].get(txn_type) == expected[txn_type]


# ------------------------------------------------------------------ generator stays in sync


def test_committed_ground_truth_matches_the_generator():
    pytest.importorskip("reportlab")                # dev-only dependency
    from scripts.make_synthetic_statements import ground_truth
    assert ground_truth() == TRUTH
