# app/services/csv_import_service.py
#
# Purpose: bulk-import expenses from a CSV. Version 2 copes with real bank
# exports: header aliases ("Narration", "Withdrawal Amt."), Indian date
# formats (15/09/2026), amounts like "Rs. 1,234.50", separate Debit/Credit
# columns, and it refuses to import the same transaction twice.
#
# Rules worth knowing:
#   * Dates with slashes are read as DAY/MONTH/YEAR (Indian banks).
#   * With a single 'amount' column, positive = expense; negative rows are
#     treated as refunds/credits and skipped.
#   * With Debit/Credit columns, only debits are imported.

import csv
import io
import logging
import re
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.transaction import Transaction

logger = logging.getLogger(__name__)

MAX_ROWS = 5000

# canonical column -> accepted header spellings (compared after lowercasing and removing dots)
HEADER_ALIASES: dict[str, set[str]] = {
    "description": {"description", "narration", "details", "particulars", "remarks", "transaction details", "transaction remarks"},
    "amount": {"amount", "amount inr", "transaction amount"},
    "debit": {"debit", "debit amount", "withdrawal", "withdrawal amt", "withdrawal amount", "dr"},
    "credit": {"credit", "credit amount", "deposit", "deposit amt", "deposit amount", "cr"},
    "date": {"date", "txn date", "transaction date", "value date", "posting date"},
}

DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y", "%d-%b-%Y", "%d %b %Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
)


def _clean_header(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", name.lower()).strip().replace("  ", " ")


def _map_headers(fieldnames: list[str]) -> dict[str, str]:
    """canonical name -> the actual header text used in this file."""
    mapping: dict[str, str] = {}
    for actual in fieldnames:
        cleaned = " ".join(_clean_header(actual).split())
        for canonical, aliases in HEADER_ALIASES.items():
            if cleaned in aliases and canonical not in mapping:
                mapping[canonical] = actual
    return mapping


def _parse_amount(text: str) -> float:
    """'Rs. 1,234.50' / '₹1,234.50' / '1234' -> float. Raises ValueError if empty or unreadable."""
    cleaned = re.sub(r"(?i)(rs\.?|inr|₹|,|\s)", "", text or "")
    if not cleaned:
        raise ValueError("empty amount")
    return float(cleaned)


def _parse_date(text: str) -> datetime:
    """Tries the known formats; the result is stored as UTC midnight/time."""
    text = (text or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{text}'")


def _decode(file_bytes: bytes) -> str:
    try:
        return file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return file_bytes.decode("cp1252", errors="replace")    # common for Excel-exported CSVs


def import_transactions_from_csv(db: Session, user_id: int, file_bytes: bytes) -> dict[str, int]:
    """
    Parses the CSV and saves new transactions. Returns counts:
    imported / skipped_invalid / skipped_duplicates / skipped_credits.
    Categories stay empty; run /categorize afterwards.
    """
    text = _decode(file_bytes)
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)

    if not reader.fieldnames:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "CSV file appears to be empty.")

    columns = _map_headers(list(reader.fieldnames))
    use_debit_credit = "debit" in columns
    has_amount = use_debit_credit or "amount" in columns
    if "description" not in columns or "date" not in columns or not has_amount:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "CSV needs a description column (description/narration/details), a date column, "
            "and an amount column (amount, or debit/withdrawal). "
            f"Found: {', '.join(reader.fieldnames)}",
        )

    parsed: list[tuple[str, float, datetime]] = []
    skipped_invalid = skipped_credits = 0

    for row_number, row in enumerate(reader, start=2):      # row 1 is the header
        if row_number - 1 > MAX_ROWS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Too many rows (limit {MAX_ROWS}). Split the file.")
        try:
            description = (row.get(columns["description"]) or "").strip()
            if not description:
                raise ValueError("empty description")
            when = _parse_date(row.get(columns["date"]) or "")

            if use_debit_credit:
                debit_text = (row.get(columns["debit"]) or "").strip()
                if not debit_text or _parse_amount(debit_text) == 0:
                    skipped_credits += 1                    # money in: salary, refund, interest
                    continue
                amount = _parse_amount(debit_text)
            else:
                amount = _parse_amount(row.get(columns["amount"]) or "")
                if amount < 0:
                    skipped_credits += 1
                    continue
                if amount == 0:
                    raise ValueError("zero amount")

            parsed.append((description[:500], amount, when))
        except ValueError as exc:
            logger.warning(f"Skipping CSV row {row_number}: {exc}")
            skipped_invalid += 1

    if not parsed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No valid expense rows found in the CSV file.")

    # Duplicate protection: skip rows already in this user's account (same text, amount and day)
    earliest, latest = min(p[2] for p in parsed), max(p[2] for p in parsed)
    existing = (
        db.query(Transaction.description, Transaction.amount, Transaction.transaction_date)
        .filter(Transaction.user_id == user_id, Transaction.transaction_date >= earliest, Transaction.transaction_date <= latest)
        .all()
    )
    existing_keys = {(d.strip().lower(), round(a, 2), dt.date()) for d, a, dt in existing}

    new_rows: list[Transaction] = []
    skipped_duplicates = 0
    for description, amount, when in parsed:
        if (description.lower(), round(amount, 2), when.date()) in existing_keys:
            skipped_duplicates += 1
            continue
        new_rows.append(Transaction(user_id=user_id, description=description, amount=amount, transaction_date=when))

    db.add_all(new_rows)
    db.commit()

    logger.info(
        f"CSV import user_id={user_id}: {len(new_rows)} imported, {skipped_invalid} invalid, "
        f"{skipped_duplicates} duplicates, {skipped_credits} credits"
    )
    return {
        "imported": len(new_rows),
        "skipped_invalid": skipped_invalid,
        "skipped_duplicates": skipped_duplicates,
        "skipped_credits": skipped_credits,
    }
