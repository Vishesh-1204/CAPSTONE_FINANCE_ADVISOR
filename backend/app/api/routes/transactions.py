# app/api/routes/transactions.py

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.transaction import (
    TransactionCreate,
    TransactionResponse,
    CategorizationSummary,
)
from app.services.transaction_service import (
    create_transaction,
    get_transactions_for_user,
    categorize_pending_transactions,
)
from app.services.csv_import_service import import_transactions_from_csv


router = APIRouter(prefix="/api/transactions", tags=["Transactions"])


@router.post("/", response_model=TransactionResponse, status_code=201)
def add_transaction(
    data: TransactionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually logs a new expense for the currently authenticated user.
    """
    return create_transaction(db, user_id=current_user.id, data=data)


@router.get("/", response_model=list[TransactionResponse])
def list_transactions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns all transactions belonging to the currently authenticated user."""
    return get_transactions_for_user(db, user_id=current_user.id)


@router.post("/upload-csv", status_code=201)
async def upload_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Bulk-imports expenses from a CSV file.
    Expected columns: description, amount, date
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a .csv file.",
        )

    file_bytes = await file.read()

    imported_count = import_transactions_from_csv(
        db,
        user_id=current_user.id,
        file_bytes=file_bytes,
    )

    return {
        "message": "CSV imported successfully.",
        "transactions_imported": imported_count,
        "next_step": "POST /api/transactions/categorize to categorize them",
    }


@router.post("/categorize", response_model=CategorizationSummary)
def categorize_transactions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Runs the Expense Intelligence Agent over all of the current user's
    uncategorized transactions.
    """
    summary = categorize_pending_transactions(
        db,
        user_id=current_user.id,
    )

    return CategorizationSummary(**summary)