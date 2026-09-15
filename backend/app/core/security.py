# app/core/security.py

from datetime import datetime, timedelta, timezone
from passlib.context import CryptContext
from jose import jwt, JWTError

from app.core.config import settings

# bcrypt is a slow-by-design hashing algorithm — that's a feature, not a bug,
# because it makes brute-forcing stolen password hashes impractical
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """Converts a plain-text password into a one-way bcrypt hash."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Checks a plain-text password against a stored hash without ever reversing the hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    """
    Creates a signed JWT for a given user id ('subject').
    The token is signed with SECRET_KEY — anyone without that key
    cannot forge a valid token, even though the payload itself is just
    base64 (not encrypted, just signed).
    """
    expire_delta = expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    expire_at = datetime.now(timezone.utc) + timedelta(minutes=expire_delta)

    payload = {"sub": subject, "exp": expire_at}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """
    Verifies a JWT's signature and expiry, and returns the user id
    stored inside it. Returns None if the token is invalid or expired.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None