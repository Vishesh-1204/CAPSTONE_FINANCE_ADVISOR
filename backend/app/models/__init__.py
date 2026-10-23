# app/models/__init__.py
#
# Purpose: importing `app.models` registers EVERY table on Base.metadata.
# Alembic (migrations/env.py), the tests and create_all all rely on this, so a
# new model must be imported here or it will be invisible to them.

from app.models.budget import Budget, BudgetRun
from app.models.financial_profile import FinancialProfile
from app.models.forecast import ForecastRun
from app.models.import_batch import ImportBatch
from app.models.investment import Holding, InvestmentPlan, InvestmentProfile
from app.models.payee_rule import PayeeRule
from app.models.transaction import Transaction
from app.models.user import User

__all__ = [
    "Budget", "BudgetRun", "FinancialProfile", "ForecastRun", "Holding", "ImportBatch", "InvestmentPlan",
    "InvestmentProfile", "PayeeRule", "Transaction", "User",
]
