# app/models/__init__.py
#
# Purpose: importing `app.models` registers EVERY table on Base.metadata.
# Alembic (migrations/env.py), the tests and create_all all rely on this, so a
# new model must be imported here or it will be invisible to them.

from app.models.import_batch import ImportBatch
from app.models.payee_rule import PayeeRule
from app.models.transaction import Transaction
from app.models.user import User

__all__ = ["ImportBatch", "PayeeRule", "Transaction", "User"]
