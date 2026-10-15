# tests/conftest.py
#
# Shared test setup. Tests run against an in-memory SQLite database, so they
# need no Postgres, no network and no API key.

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base_class import Base
from app.models.transaction import Transaction
from app.models.user import User


@pytest.fixture()
def db():
    """A fresh empty database with two users (ids 1 and 2) for every test."""
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([
        User(email="a@example.com", full_name="User A", hashed_password="x"),
        User(email="b@example.com", full_name="User B", hashed_password="x"),
    ])
    session.commit()
    yield session
    session.close()
    engine.dispose()


def add_tx(
    db, description: str, amount: float = 100.0, user_id: int = 1, day: int = 5, direction: str = "debit"
) -> Transaction:
    """Test helper: insert one uncategorized transaction (txn_type left NULL, like a pre-v3 row)."""
    tx = Transaction(
        user_id=user_id, description=description, amount=amount, direction=direction,
        transaction_date=datetime(2026, 9, day, tzinfo=timezone.utc),
    )
    db.add(tx)
    db.commit()
    return tx
