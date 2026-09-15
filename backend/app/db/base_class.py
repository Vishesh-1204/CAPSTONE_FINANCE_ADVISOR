# app/db/base_class.py

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """
    Shared base class for all SQLAlchemy models in the app.
    Every table (User, Transaction, Budget, ...) inherits from this,
    which lets SQLAlchemy discover and create all tables together.
    """
    pass