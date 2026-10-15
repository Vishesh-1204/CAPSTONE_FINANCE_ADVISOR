# app/api/routes/transactions.py
#
# Purpose: HTTP endpoints for transactions. Thin on purpose: parse the request,
# call a service, return the result. Every route depends on get_current_user,
# and user_id always comes from the verified JWT, never from the request body.
#
# Route order matters: fixed paths (/summary, /bulk-category) are declared
# before /{transaction_id}/... so "summary" is never read as an id.

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.routes.statements import read_upload
from app.db.session import get_db
from app.models.user import User
from app.schemas.transaction import (
    BulkCategoryRequest,
    BulkCategoryResponse,
    CategorizationSummary,
    CategoryCorrection,
    CorrectionResponse,
    CsvImportSummary,
    MonthlySummary,
    TransactionCreate,
    TransactionPage,
    TransactionResponse,
)
from app.services.categorization_service import categorize_pending_transactions
from app.services.category_override_service import apply_user_correction, bulk_set_category, unlock_transaction
from app.services.statement_import_service import import_statement
from app.services.summary_service import monthly_summary
from app.services.transaction_service import TransactionFilters, create_transaction, list_transactions

router = APIRouter(prefix="/api/transactions", tags=["Transactions"])

MAX_UPLOAD_BYTES = 5 * 1024 * 1024   # 5 MB is plenty for a bank statement
MAX_PAGE_SIZE = 500

StatusFilter = Literal["needs_review", "uncategorized", "categorized", "locked"]
TxnTypeFilter = Literal["expense", "income", "investment_out", "investment_in", "transfer"]


@router.post("/", response_model=TransactionResponse, status_code=201)
def add_transaction(
    data: TransactionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually logs one transaction (debit by default) for the logged-in user."""
    return create_transaction(db, user_id=current_user.id, data=data)


@router.get("/", response_model=TransactionPage)
def get_transactions(
    status: StatusFilter | None = None,
    txn_type: TxnTypeFilter | None = None,
    category: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    needs_review: bool | None = Query(None, description="Deprecated: use status=needs_review"),
    limit: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lists the logged-in user's transactions with filters and paging.
    status: needs_review (review queue, least confident first) | uncategorized |
    categorized | locked. date_to is inclusive.
    """
    filters = TransactionFilters(
        status=status, txn_type=txn_type, category=category, date_from=date_from, date_to=date_to,
        needs_review=needs_review, limit=limit, offset=offset,
    )
    total, items = list_transactions(db, user_id=current_user.id, filters=filters)
    return TransactionPage(total=total, limit=limit, offset=offset, items=items)


@router.get("/summary", response_model=MonthlySummary)
def get_summary(
    month: str | None = Query(None, description="YYYY-MM; defaults to the current month", examples=["2026-09"]),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Monthly totals. Only expenses count as spending: investments, redemptions and
    self-transfers are reported separately.
    """
    return monthly_summary(db, user_id=current_user.id, month=month)


@router.post("/upload-csv", response_model=CsvImportSummary, status_code=201)
async def upload_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Backward-compatible alias of POST /api/statements/upload for CSV files
    (same pipeline; credits are now imported too). Prefer the new endpoint.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Please upload a .csv file.")

    file_bytes = await read_upload(file, MAX_UPLOAD_BYTES)
    summary = import_statement(db, user_id=current_user.id, filename=file.filename, file_bytes=file_bytes)
    return CsvImportSummary(
        imported=summary["rows_imported"],
        skipped_invalid=summary["rows_invalid"],
        skipped_duplicates=summary["rows_duplicate"],
        skipped_credits=0,
        import_batch_id=summary["import_batch_id"],
        next_step=summary["next_step"],
    )


@router.post("/categorize", response_model=CategorizationSummary)
def categorize_transactions(
    reset_ai_results: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Runs the Expense Intelligence Agent over all uncategorized, unlocked transactions.
    Pass ?reset_ai_results=true to redo every UNLOCKED category
    (categories you set yourself are locked and always kept).

    Runs synchronously and is rate-limited by the LLM provider, so big batches
    take a while. Phase 9 moves this to a Celery background task.
    """
    summary = categorize_pending_transactions(db, user_id=current_user.id, reset_ai_results=reset_ai_results)
    return CategorizationSummary(**summary)


@router.post("/bulk-category", response_model=BulkCategoryResponse)
def bulk_category(
    data: BulkCategoryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Sets one category on many of your transactions at once; they become locked."""
    return bulk_set_category(db, user_id=current_user.id, transaction_ids=data.transaction_ids, category=data.category)


@router.patch("/{transaction_id}/category", response_model=CorrectionResponse)
def correct_category(
    transaction_id: int,
    data: CategoryCorrection,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Sets (or confirms) the category of one transaction and locks it, so the agent
    never overwrites it. Optional:
      * apply_to_similar: also update your other unlocked rows of the same payee
      * remember_payee: create a rule so future rows of this payee get this category
    """
    outcome = apply_user_correction(
        db,
        user_id=current_user.id,
        transaction_id=transaction_id,
        category=data.category,
        apply_to_similar=data.apply_to_similar,
        remember_payee=data.remember_payee,
    )
    return CorrectionResponse(
        transaction=outcome.transaction, similar_updated=outcome.similar_updated, payee_rule=outcome.payee_rule
    )


@router.post("/{transaction_id}/unlock", response_model=TransactionResponse)
def unlock(
    transaction_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Removes the lock. The category stays; the agent may redo it on the next
    /categorize?reset_ai_results=true run.
    """
    return unlock_transaction(db, user_id=current_user.id, transaction_id=transaction_id)
