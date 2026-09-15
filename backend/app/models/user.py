# app/models/user.py

from sqlalchemy import String, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from app.db.base_class import Base


class User(Base):
    """
    Represents a registered user of the finance advisor app.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # unique=True enforces one account per email at the database level
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # We NEVER store the raw password — only its bcrypt hash
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())