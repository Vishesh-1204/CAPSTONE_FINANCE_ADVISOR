# app/schemas/token.py

from pydantic import BaseModel
from typing import Optional


class Token(BaseModel):
    """Response shape returned to the client after successful login."""
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """
    Data encoded INSIDE the JWT.
    'sub' (subject) is the JWT standard field for identifying who the token belongs to —
    we store the user's id there.
    """
    sub: Optional[str] = None