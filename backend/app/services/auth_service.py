# app/services/auth_service.py

import logging
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.user import User
from app.schemas.user import UserCreate
from app.core.security import hash_password, verify_password

logger = logging.getLogger(__name__)


def get_user_by_email(db: Session, email: str) -> User | None:
    """Fetches a user row by email, or None if it doesn't exist."""
    return db.query(User).filter(User.email == email).first()


def register_user(db: Session, user_data: UserCreate) -> User:
    """
    Creates a new user after checking the email isn't already taken.
    Raises a 400 error if it is — this is the correct HTTP status
    for 'the request is invalid because of something the client sent'.
    """
    existing_user = get_user_by_email(db, user_data.email)
    if existing_user:
        logger.warning(f"Registration attempted with existing email: {user_data.email}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email already exists.",
        )

    new_user = User(
        email=user_data.email,
        full_name=user_data.full_name,
        hashed_password=hash_password(user_data.password),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)  # loads the auto-generated id and created_at back into the object

    logger.info(f"New user registered: {new_user.email}")
    return new_user


def authenticate_user(db: Session, email: str, password: str) -> User:
    """
    Verifies email + password. Raises a 401 error on any failure.
    Deliberately uses the SAME error message for 'no such user' and
    'wrong password' — telling an attacker WHICH one was wrong would
    let them discover which emails are registered (an information leak).
    """
    user = get_user_by_email(db, email)

    if not user or not verify_password(password, user.hashed_password):
        logger.warning(f"Failed login attempt for email: {email}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user