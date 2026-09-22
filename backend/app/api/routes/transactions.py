# app/api/routes/transactions.py

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.transaction import TransactionCreate, TransactionResponse
from app.services.transaction_service import create_transaction, get_transactions_for_user

router = APIRouter(prefix="/api/transactions", tags=["Transactions"])


@router.post("/", response_model=TransactionResponse, status_code=201)
def add_transaction(
    data: TransactionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually logs a new expense for the currently authenticated user.
    'current_user' comes from decoding the JWT in the Authorization header —
    the client never sends a user_id directly, which prevents someone
    from logging an expense under someone else's account.
    """
    return create_transaction(db, user_id=current_user.id, data=data)


@router.get("/", response_model=list[TransactionResponse])
def list_transactions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Returns all transactions belonging to the currently authenticated user."""
    return get_transactions_for_user(db, user_id=current_user.id)