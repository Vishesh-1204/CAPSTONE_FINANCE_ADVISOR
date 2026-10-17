# app/services/statement_import_service.py
#
# Purpose: the ONE import pipeline every statement format goes through
# (spec section 5). A parser (CSV / XLSX / PDF) has already produced validated
# ParsedRows; this service then:
#
#   1. checks the running balance of every row (a parsing-quality signal)
#   2. de-duplicates against the user's existing rows (same narration, amount,
#      day and direction), across ALL formats
#   3. records an ImportBatch (what this upload did; also a research metric)
#   4. saves new Transactions (debits AND credits) with balance + batch id
#   5. enriches them (channel, payee_key, txn_type) via enrich_transaction
#
# Categorization stays a separate step (POST /api/transactions/categorize).
# Privacy: everything is local; logs contain counts and ids only.

import logging
import re
from collections import Counter
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.money import to_money
from app.models.import_batch import ImportBatch
from app.models.transaction import Transaction
from app.services.statement_parsers import ParsedRow, ParseResult, StatementParseError, get_parser
from app.services.transaction_enrichment import enrich_transaction

logger = logging.getLogger(__name__)

NEXT_STEP = "POST /api/transactions/categorize"

DedupeKey = tuple[str, Decimal, date, str]

BALANCE_TOLERANCE = Decimal("0.01")
MAX_REPORTED_MISMATCHES = 10


def check_balances(rows: list[ParsedRow], opening_balance: Decimal | None = None) -> list[int]:
    """
    Verifies previous_balance - debit + credit == printed balance (within 1 paisa)
    for every row, sets row.balance_ok, and returns the source row numbers that
    did NOT add up. A mismatch usually means a misread amount or a skipped row.

    Statements may be newest-first: if the first date is after the last one, the
    rows are checked in reverse. The first row can only be checked when the file
    has an "Opening Balance" line; otherwise its balance_ok stays None.
    After a mismatch the chain restarts from the printed balance, so ONE bad row
    produces one mismatch, not a cascade.
    """
    ordered = list(reversed(rows)) if rows and rows[0].date > rows[-1].date else rows
    previous = opening_balance
    mismatches: list[int] = []
    for row in ordered:
        if row.balance is None:
            row.balance_ok, previous = None, None          # chain broken: nothing to compare with
            continue
        if previous is None:
            row.balance_ok = None
        else:
            expected = previous - (row.debit or 0) + (row.credit or 0)
            row.balance_ok = abs(expected - row.balance) <= BALANCE_TOLERANCE
            if not row.balance_ok:
                mismatches.append(row.row_number)
        previous = row.balance
    return sorted(mismatches)


_WHITESPACE = re.compile(r"\s+")


def _dedupe_key(narration: str, amount: Decimal, when: date, direction: str) -> DedupeKey:
    """
    Same text, same amount (exact, Decimal), same day, same direction = the same
    transaction. Whitespace is ignored: a PDF may print "swiggy @icici" where the
    CSV export of the same statement says "swiggy@icici".
    """
    return _WHITESPACE.sub("", narration.lower()), to_money(amount), when, direction


def _existing_keys(db: Session, user_id: int, rows: list[ParsedRow]) -> set[DedupeKey]:
    """Keys of the user's rows already stored in the date range of this file."""
    earliest = min(r.date for r in rows)
    latest = max(r.date for r in rows)
    existing = (
        db.query(Transaction.description, Transaction.amount, Transaction.transaction_date, Transaction.direction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= earliest,
            Transaction.transaction_date <= latest,
        )
        .all()
    )
    return {_dedupe_key(d, a, dt.date(), direction) for d, a, dt, direction in existing}


def _parse(filename: str, file_bytes: bytes, password: str | None) -> tuple[str, ParseResult]:
    """Runs the right parser and turns its errors into HTTP errors (the only HTTP-aware step)."""
    try:
        parser = get_parser(filename)
        return parser.source_type, parser.parse(file_bytes, password=password)
    except StatementParseError as error:
        raise HTTPException(status_code=error.status_code, detail=error.message) from None


def import_statement(
    db: Session, user_id: int, filename: str, file_bytes: bytes, password: str | None = None
) -> dict:
    """
    Parses one uploaded statement and saves its new transactions for `user_id`.
    Returns the import summary (see StatementImportSummary). The password, if
    any, is only passed to the parser and is never stored or logged.
    """
    source_type, result = _parse(filename, file_bytes, password)
    if not result.rows:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No valid transaction rows found in the file.")

    balance_checked = result.has_balance and any(r.balance is not None for r in result.rows)
    mismatch_rows = check_balances(result.rows, result.opening_balance) if balance_checked else []
    existing = _existing_keys(db, user_id, result.rows)

    batch = ImportBatch(
        user_id=user_id,
        filename=(filename or "upload")[:255],
        source_type=source_type,
        layout=result.layout,
        rows_total=len(result.rows) + result.invalid_rows,
        rows_invalid=result.invalid_rows,
        balance_checked=balance_checked,
        balance_mismatches=len(mismatch_rows),
    )
    db.add(batch)
    db.flush()                                   # gives batch.id for the transactions below

    new_rows: list[Transaction] = []
    duplicates = 0
    for row in result.rows:
        if _dedupe_key(row.narration, row.amount, row.date.date(), row.direction) in existing:
            duplicates += 1
            continue
        transaction = Transaction(
            user_id=user_id,
            description=row.narration,
            amount=row.amount,
            direction=row.direction,
            balance=row.balance,
            balance_ok=row.balance_ok,
            transaction_date=row.date,
            import_batch_id=batch.id,
        )
        enrich_transaction(transaction)          # channel, payee_key, txn_type (local, no LLM)
        new_rows.append(transaction)

    batch.rows_imported = len(new_rows)
    batch.rows_duplicate = duplicates
    db.add_all(new_rows)
    db.commit()
    db.refresh(batch)

    by_txn_type = dict(Counter(t.txn_type for t in new_rows))
    logger.info(
        f"Statement import batch={batch.id} user_id={user_id} type={source_type} layout={result.layout}: "
        f"{batch.rows_imported} imported, {duplicates} duplicates, {result.invalid_rows} invalid, "
        f"{len(mismatch_rows)} balance mismatches"
    )
    return {
        "import_batch_id": batch.id,
        "filename": batch.filename,
        "source_type": source_type,
        "layout": result.layout,
        "rows_total": batch.rows_total,
        "rows_imported": batch.rows_imported,
        "rows_duplicate": duplicates,
        "rows_invalid": result.invalid_rows,
        "by_txn_type": by_txn_type,
        "balance_checked": batch.balance_checked,
        "balance_mismatches": batch.balance_mismatches,
        "balance_mismatch_rows": mismatch_rows[:MAX_REPORTED_MISMATCHES],
        "next_step": NEXT_STEP,
    }


def list_batches(db: Session, user_id: int) -> list[ImportBatch]:
    """The user's uploads, newest first."""
    return db.query(ImportBatch).filter(ImportBatch.user_id == user_id).order_by(ImportBatch.id.desc()).all()


def get_batch(db: Session, user_id: int, batch_id: int) -> ImportBatch:
    """One of the user's uploads; someone else's id is a 404."""
    batch = db.query(ImportBatch).filter(ImportBatch.id == batch_id, ImportBatch.user_id == user_id).first()
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found.")
    return batch
