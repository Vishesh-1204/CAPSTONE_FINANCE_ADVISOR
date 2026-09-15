# app/schemas/user.py

from pydantic import BaseModel, EmailStr, ConfigDict
from datetime import datetime


class UserCreate(BaseModel):
    """Shape of data required to register a new user."""
    email: EmailStr
    full_name: str
    password: str  # plain text, only used briefly to compute the hash


class UserResponse(BaseModel):
    """Shape of user data we're allowed to send back to the client."""
    id: int
    email: EmailStr
    full_name: str
    is_active: bool
    created_at: datetime

    # Allows Pydantic to read this straight from a SQLAlchemy object
    model_config = ConfigDict(from_attributes=True)