# tests/conftest.py
#
# Shared test setup. Tests run against an in-memory SQLite database, so they
# need no Postgres, no network and no API key.

import calendar
from datetime import date, datetime, timezone
from decimal import Decimal

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


def add_expense(
    db,
    category: str | None,
    amount: float | str,
    month: str = "2026-09",
    day: int = 5,
    user_id: int = 1,
    txn_type: str | None = "expense",
    direction: str = "debit",
    needs_review: bool = False,
    description: str = "Synthetic merchant",
) -> Transaction:
    """Test helper: insert one already-categorized row in `month` ('YYYY-MM')."""
    year, month_number = (int(part) for part in month.split("-"))
    tx = Transaction(
        user_id=user_id, description=description, amount=Decimal(str(amount)), direction=direction,
        txn_type=txn_type, category=category, needs_review=needs_review,
        transaction_date=datetime(year, month_number, day, 12, tzinfo=timezone.utc),
    )
    db.add(tx)
    db.commit()
    return tx


# --- Phase 5 (forecast) helpers ---

# The standard forecast persona: June-September 2026 history; "today" is 22 October 2026
HISTORY_MONTHS: tuple[str, ...] = ("2026-06", "2026-07", "2026-08", "2026-09")
FORECAST_AS_OF = date(2026, 10, 22)


def month_day(month: str, day: int) -> date:
    """('2026-09', 5) -> date(2026, 9, 5)"""
    year, mon = (int(part) for part in month.split("-"))
    return date(year, mon, day)


def add_txn(db, on: date, amount, *, direction: str = "debit", txn_type: str | None = "expense",
            category: str | None = None, description: str = "POS SYNTH SHOP", balance=None, user_id: int = 1,
            payee_key: str | None = None) -> Transaction:
    """Test helper: insert one transaction on a given date, optionally with a statement balance."""
    tx = Transaction(
        user_id=user_id, description=description, amount=Decimal(str(amount)), direction=direction,
        txn_type=txn_type, category=category, payee_key=payee_key,
        balance=Decimal(str(balance)) if balance is not None else None,
        transaction_date=datetime(on.year, on.month, on.day, 12, tzinfo=timezone.utc),
    )
    db.add(tx)
    db.commit()
    return tx


def seed_forecast_persona(db, user_id: int = 1, groceries=(5000, 6000, 5500, 6500),
                          history: tuple[str, ...] = HISTORY_MONTHS, current: str = "2026-10") -> None:
    """
    A synthetic salaried user: salary on the 1st (month name in the narration),
    UPI rent of 18,000 on the 5th, a 3,000 SIP on the 10th, and variable groceries
    on the 12th (with a statement balance of 100000 + month index), for every
    history month plus the first days of `current`. Also a refund, a self-transfer
    and a redemption, which are neither income nor spending.
    """
    for index, month in enumerate(history + (current,)):
        name = calendar.month_abbr[int(month[5:])].upper()
        add_txn(db, month_day(month, 1), 60000, direction="credit", txn_type="income", category="Salary",
                description=f"NEFT-ACME CORP SALARY {name} {month[:4]}", user_id=user_id)
        add_txn(db, month_day(month, 5), 18000, category="Housing & Rent", user_id=user_id,
                description=f"UPI/41234567{index}/LANDLORD/landlord.r@okaxis")
        add_txn(db, month_day(month, 10), 3000, txn_type="investment_out", category="Investments & Savings",
                description="NACH/SYNTH MF SIP/FOLIO 0000001", user_id=user_id)
        grocery = groceries[index] if index < len(groceries) else 2000
        add_txn(db, month_day(month, 12), grocery, category="Groceries", description="POS SYNTH KIRANA STORE",
                user_id=user_id, balance=100000 + index)
    add_txn(db, date(2026, 7, 18), 1200, direction="credit", txn_type="income", category="Refund & Cashback",
            description="REFUND SYNTH ONLINE MARKETPLACE", user_id=user_id)
    add_txn(db, date(2026, 8, 20), 10000, txn_type="transfer", category="Transfers",
            description="IMPS/OWN ACCOUNT TRANSFER SELF", user_id=user_id)
    add_txn(db, date(2026, 7, 22), 6000, direction="credit", txn_type="investment_in",
            category="Investment Redemption", description="SYNTH MF REDEMPTION PAYOUT", user_id=user_id)
