# app/services/transaction_filters.py
#
# Purpose: the shared "what counts as what" rules, as SQL conditions, so the
# Budget agent (Phase 4) and the Forecast agent (Phase 5) can never disagree
# about which rows are spending or earnings. Moved here from budget_service.py.
#
#   EXPENSE_FILTER     spending: txn_type='expense', or a pre-v3 row (txn_type
#                      still NULL) that is a debit
#   INCOME_FILTER      earnings: income credits, refunds excluded (a refund is
#                      money returned, not earned)
#   INVESTMENT_OUT_FILTER  money put into investments (SIPs, top-ups): saved, not spent
#
# Investments in/out, redemptions and own-account transfers are never spending.
# Use them inside .filter(...) together with a user_id condition.

from sqlalchemy import and_, or_

from app.core.categories import REFUND_CATEGORY
from app.core.transaction_types import CREDIT, DEBIT, TXN_EXPENSE, TXN_INCOME, TXN_INVESTMENT_OUT
from app.models.transaction import Transaction

EXPENSE_FILTER = or_(
    Transaction.txn_type == TXN_EXPENSE,
    and_(Transaction.txn_type.is_(None), Transaction.direction == DEBIT),
)

# The explicit NULL check is needed because SQL's `category != 'x'` is never true for NULL.
INCOME_FILTER = and_(
    Transaction.txn_type == TXN_INCOME,
    Transaction.direction == CREDIT,
    or_(Transaction.category.is_(None), Transaction.category != REFUND_CATEGORY),
)

INVESTMENT_OUT_FILTER = Transaction.txn_type == TXN_INVESTMENT_OUT
