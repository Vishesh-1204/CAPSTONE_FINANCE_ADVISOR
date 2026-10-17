# app/services/summary_service.py
#
# Purpose: the monthly money picture for GET /api/transactions/summary.
#
# The key rule: only txn_type='expense' counts as SPENDING. Investments
# (SIP, broker top-ups), redemptions and self-transfers are reported in their
# own buckets, so a disciplined investor is not told they overspend.
#
#   savings_investment_rate = (income_total - expense_total) / income_total
#   i.e. the share of income NOT spent; money invested counts as saved.
#   None when there was no income that month (the ratio is undefined).
#
# Known limitation (for Phase 6): net_investment_flow = invested - redeemed is
# a cash-flow number, not a return; real returns need broker holdings.

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.money import to_money
from app.core.transaction_types import (
    CREDIT, TXN_EXPENSE, TXN_INCOME, TXN_INVESTMENT_IN, TXN_INVESTMENT_OUT, TXN_TRANSFER,
)
from app.models.transaction import Transaction

UNCATEGORIZED_LABEL = "Uncategorized"


def _month_bounds(month: str | None) -> tuple[str, datetime, datetime]:
    """'2026-09' -> ('2026-09', 2026-09-01T00:00Z, 2026-10-01T00:00Z). Default: the current UTC month."""
    if month is None:
        now = datetime.now(timezone.utc)
        year, month_number = now.year, now.month
    else:
        try:
            parsed = datetime.strptime(month, "%Y-%m")
        except ValueError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "month must look like YYYY-MM, e.g. 2026-09.") from None
        year, month_number = parsed.year, parsed.month
    start = datetime(year, month_number, 1, tzinfo=timezone.utc)
    end = datetime(year + (month_number == 12), month_number % 12 + 1, 1, tzinfo=timezone.utc)
    return f"{year:04d}-{month_number:02d}", start, end


def _effective_type(direction: str, txn_type: str | None) -> str:
    """Rows not yet typed (pre-v3, before a categorize run) count as expense/income by direction."""
    if txn_type:
        return txn_type
    return TXN_INCOME if direction == CREDIT else TXN_EXPENSE


def monthly_summary(db: Session, user_id: int, month: str | None = None) -> dict:
    """Totals per transaction type and expense totals per category for one month."""
    label, start, end = _month_bounds(month)
    rows = (
        db.query(
            Transaction.direction,
            Transaction.txn_type,
            Transaction.category,
            func.sum(Transaction.amount),
            func.count(Transaction.id),
        )
        .filter(Transaction.user_id == user_id, Transaction.transaction_date >= start, Transaction.transaction_date < end)
        .group_by(Transaction.direction, Transaction.txn_type, Transaction.category)
        .all()
    )

    totals: dict[str, Decimal] = {t: Decimal("0.00") for t in (
        TXN_EXPENSE, TXN_INCOME, TXN_INVESTMENT_OUT, TXN_INVESTMENT_IN, TXN_TRANSFER,
    )}
    by_category: dict[str, dict] = {}
    transaction_count = 0
    for direction, txn_type, category, amount_sum, count in rows:
        amount = to_money(amount_sum or 0)
        kind = _effective_type(direction, txn_type)
        totals[kind] = totals.get(kind, Decimal("0.00")) + amount
        transaction_count += count
        if kind == TXN_EXPENSE:
            bucket = by_category.setdefault(category or UNCATEGORIZED_LABEL, {"total": Decimal("0.00"), "count": 0})
            bucket["total"] += amount
            bucket["count"] += count

    income, expense = totals[TXN_INCOME], totals[TXN_EXPENSE]
    rate = round(float((income - expense) / income), 4) if income > 0 else None

    return {
        "month": label,
        "transaction_count": transaction_count,
        "expense_total": float(expense),
        "income_total": float(income),
        "investment_out_total": float(totals[TXN_INVESTMENT_OUT]),
        "redemption_total": float(totals[TXN_INVESTMENT_IN]),
        "transfer_total": float(totals[TXN_TRANSFER]),
        "net_investment_flow": float(totals[TXN_INVESTMENT_OUT] - totals[TXN_INVESTMENT_IN]),
        "savings_investment_rate": rate,
        "by_category": sorted(
            ({"category": name, "total": float(v["total"]), "count": v["count"]} for name, v in by_category.items()),
            key=lambda item: item["total"],
            reverse=True,
        ),
    }
