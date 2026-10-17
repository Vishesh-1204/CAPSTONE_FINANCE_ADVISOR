# app/api/routes/statements.py
#
# Purpose: statement upload (CSV today; XLSX and PDF from S5) and the list of
# past uploads (import batches). HTTP only: all work happens in
# statement_import_service. The optional PDF password arrives as a form field,
# is passed straight to the parser and is never stored or logged.

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.statement import ImportBatchResponse, StatementImportSummary
from app.services.statement_import_service import get_batch, import_statement, list_batches

router = APIRouter(tags=["Statements"])

MAX_STATEMENT_BYTES = 10 * 1024 * 1024    # PDFs are larger than CSVs


async def read_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Reads an upload, refusing files above the size limit (413)."""
    file_bytes = await file.read(max_bytes + 1)
    if len(file_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File too large (limit {max_bytes // (1024 * 1024)} MB).")
    return file_bytes


@router.post("/api/statements/upload", response_model=StatementImportSummary, status_code=201)
async def upload_statement(
    file: UploadFile = File(...),
    password: str | None = Form(None, description="Only for password-protected PDFs. Never stored."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Imports a bank statement. Debits AND credits are saved; rows already in your
    account are skipped. Rows start uncategorized: run POST /api/transactions/categorize next.
    """
    file_bytes = await read_upload(file, MAX_STATEMENT_BYTES)
    return import_statement(
        db, user_id=current_user.id, filename=file.filename or "", file_bytes=file_bytes, password=password or None
    )


@router.get("/api/import-batches", response_model=list[ImportBatchResponse])
def get_import_batches(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Your statement uploads, newest first."""
    return list_batches(db, user_id=current_user.id)


@router.get("/api/import-batches/{batch_id}", response_model=ImportBatchResponse)
def get_import_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One of your statement uploads."""
    return get_batch(db, user_id=current_user.id, batch_id=batch_id)
