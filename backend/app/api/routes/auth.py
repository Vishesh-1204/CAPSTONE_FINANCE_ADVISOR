# app/api/routes/auth.py

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.user import UserCreate, UserResponse
from app.schemas.token import Token
from app.services.auth_service import register_user, authenticate_user
from app.core.security import create_access_token

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/register", response_model=UserResponse, status_code=201)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """
    Registers a new user.
    Returns the created user (without the password hash).
    """
    new_user = register_user(db, user_data)
    return new_user


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """
    Logs a user in using standard OAuth2 password flow.
    NOTE: OAuth2PasswordRequestForm expects 'username' as the field name
    even though we're using email — we simply treat 'username' as the email here.
    """
    user = authenticate_user(db, email=form_data.username, password=form_data.password)
    access_token = create_access_token(subject=str(user.id))
    return Token(access_token=access_token)