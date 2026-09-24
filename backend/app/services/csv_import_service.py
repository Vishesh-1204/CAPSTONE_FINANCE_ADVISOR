# app/services/csv_import_service.py

import csv
import io
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.transaction import Transaction

logger = logging.getLogger(__name__)

# The CSV columns we require. Keeping this explicit gives users a
# clear error instead of a confusing crash on a malformed file.
REQUIRED_COLUMNS = {"description", "amount", "date"}


def import_transactions_from_csv(db: Session, user_id: int, file_bytes: bytes) -> int:
    """
    Parses a CSV file and bulk-inserts transactions for a user.

    Expected CSV format:
        description,amount,date
        Swiggy order,450.00,2026-09-15

    Returns the number of rows successfully imported.
    Categories are left empty — run the categorization endpoint afterwards.
    """
    try:
        # Decode raw upload bytes into text the csv module can read
        text_stream = io.StringIO(file_bytes.decode("utf-8-sig"))
        reader = csv.DictReader(text_stream)

        if reader.fieldnames is None:
            raise ValueError("CSV file appears to be empty.")

        # Normalize headers so 'Description' and 'description' both work
        headers = {name.strip().lower() for name in reader.fieldnames}
        missing = REQUIRED_COLUMNS - headers
        if missing:
            raise ValueError(f"CSV is missing required column(s): {', '.join(sorted(missing))}")

        transactions: list[Transaction] = []
        skipped_rows = 0

        for row_number, row in enumerate(reader, start=2):  # start=2 because row 1 is the header
            # Normalize this row's keys to lowercase
            clean_row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}

            try:
                transactions.append(Transaction(
                    user_id=user_id,
                    description=clean_row["description"],
                    amount=float(clean_row["amount"]),
                    transaction_date=datetime.fromisoformat(clean_row["date"]),
                ))
            except (ValueError, KeyError) as row_error:
                # One bad row shouldn't abort the entire import
                logger.warning(f"Skipping CSV row {row_number}: {row_error}")
                skipped_rows += 1

        if not transactions:
            raise ValueError("No valid rows found in the CSV file.")

        db.add_all(transactions)
        db.commit()

        logger.info(
            f"CSV import for user_id={user_id}: "
            f"{len(transactions)} imported, {skipped_rows} skipped"
        )
        return len(transactions)

    except ValueError as exc:
        # Client-side problem (bad file) -> 400, not 500
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File could not be read as UTF-8 text. Please upload a valid CSV.",
        )
