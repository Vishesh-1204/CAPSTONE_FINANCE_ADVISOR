# app/api/routes/transactions.py
#
# Purpose: HTTP endpoints for transactions. Thin on purpose: parse the request,
# call a service, return the result. Every route depends on get_current_user,
# and user_id always comes from the verified JWT, never from the request body.

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.transaction import (
    CategorizationSummary,
    CategoryCorrection,
    CorrectionResponse,
    CsvImportSummary,
    TransactionCreate,
    TransactionResponse,
)
from app.services.categorization_service import apply_user_correction, categorize_pending_transactions
from app.services.csv_import_service import import_transactions_from_csv
from app.services.transaction_service import create_transaction, get_transactions_for_user

router = APIRouter(prefix="/api/transactions", tags=["Transactions"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024   # 5 MB is plenty for a bank statement


@router.post("/", response_model=TransactionResponse, status_code=201)
def add_transaction(
    data: TransactionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually logs one expense for the logged-in user."""
    return create_transaction(db, user_id=current_user.id, data=data)


@router.get("/", response_model=list[TransactionResponse])
def list_transactions(
    needs_review: bool | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lists the logged-in user's transactions.
    Use ?needs_review=true to see only the ones the agent was unsure about.
    """
    return get_transactions_for_user(db, user_id=current_user.id, needs_review=needs_review)


@router.post("/upload-csv", response_model=CsvImportSummary, status_code=201)
async def upload_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Bulk-imports expenses from a CSV (bank export or description,amount,date).
    Duplicates already in your account are skipped. Rows start uncategorized.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")

    file_bytes = await file.read()
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (limit 5 MB).")

    counts = import_transactions_from_csv(db, user_id=current_user.id, file_bytes=file_bytes)
    return CsvImportSummary(**counts, next_step="POST /api/transactions/categorize")


@router.post("/categorize", response_model=CategorizationSummary)
def categorize_transactions(
    reset_ai_results: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Runs the Expense Intelligence Agent over all uncategorized transactions.
    Pass ?reset_ai_results=true to redo every category the agent chose
    (categories you confirmed yourself are always kept).

    Runs synchronously and is rate-limited by the LLM provider, so big batches
    take a while. Phase 9 moves this to a Celery background task.
    """
    summary = categorize_pending_transactions(db, user_id=current_user.id, reset_ai_results=reset_ai_results)
    return CategorizationSummary(**summary)


@router.patch("/{transaction_id}/category", response_model=CorrectionResponse)
def correct_category(
    transaction_id: int,
    data: CategoryCorrection,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Fixes (or confirms) the category of one transaction. The agent remembers it:
    the same description is categorized correctly next time, and similar
    already-categorized rows are fixed too (apply_to_similar=true).
    """
    transaction, similar_updated = apply_user_correction(
        db,
        user_id=current_user.id,
        transaction_id=transaction_id,
        category=data.category,
        apply_to_similar=data.apply_to_similar,
    )
    return CorrectionResponse(transaction=transaction, similar_updated=similar_updated)
