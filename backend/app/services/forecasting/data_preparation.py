# app/services/forecasting/data_preparation.py
#
# Purpose: the ONLY forecasting module that reads the database. It turns a
# user's transactions into the plain inputs the pure modules need:
#
#   1. the monthly history span (complete months that actually have data)
#   2. recurring items (salary, rent/EMI/subscriptions, SIPs), detected per kind
#   3. the monthly VARIABLE spending series = expenses minus regular payments of
#      recurring fixed expenses (so rent is not forecast twice)
#   4. the opening balance (request -> latest statement balance -> HTTP 400)
#   5. where the expected income comes from (recurring credits -> profile -> none)
#
# Rules (docs/phase5-spec.md section 3, agreed changes at F0):
#   * Spending/income use the shared SQL filters (transaction_filters.py), the
#     same rules as the Budget agent. Refunds, redemptions and own-account
#     transfers are neither income nor spending here.
#   * Only COMPLETE months before as_of are history. If statements stop earlier
#     (e.g. last import ends in September, forecast made in November), the
#     history ends at the last month WITH data; the empty months in between are
#     not fake zeros. Empty months INSIDE the history count as 0.
#   * Every query filters by user_id and ignores rows after as_of (clock injection).

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.money import to_money
from app.core.transaction_types import CREDIT, DEBIT
from app.models.forecast import (
    INCOME_FROM_PROFILE, INCOME_FROM_RECURRING, INCOME_NONE, OPENING_FROM_REQUEST, OPENING_FROM_STATEMENT,
)
from app.models.transaction import Transaction
from app.services.budget_service import get_profile
from app.services.forecasting.horizon import month_start, next_month
from app.services.forecasting.recurrence_detector import (
    Event, RecurringItem, detect_recurring, is_active, is_occurrence_of,
)
from app.services.narration_parser import parse_narration
from app.services.transaction_filters import EXPENSE_FILTER, INCOME_FILTER, INVESTMENT_OUT_FILTER

logger = logging.getLogger(__name__)

# What a recurring item is (the RecurringKind values in schemas/forecast.py)
KIND_INCOME = "income"
KIND_FIXED_EXPENSE = "fixed_expense"
KIND_INVESTMENT = "investment"

# Month words dropped from a payee key: "acme corp salary oct" and "... nov" are the same payer
_MONTH_WORDS = frozenset({
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "january", "february", "march", "april", "june", "july", "august", "september", "october",
    "november", "december",
})


# --- Small helpers ---

def recurrence_key(payee_key: str | None, description: str) -> str:
    """
    The key recurring payments are grouped by: the stored payee_key without
    month words. Old rows with no payee_key get one from the narration. A UPI
    ID (has '@') is already stable and is returned unchanged. The stored
    payee_key itself is never modified (payee rules depend on it).
    """
    key = payee_key or parse_narration(description).payee_key
    if "@" in key:
        return key
    kept = [token for token in key.split() if token not in _MONTH_WORDS]
    return " ".join(kept) or key


def to_utc_date(moment: datetime) -> date:
    """The calendar date of a stored timestamp, read in UTC (naive SQLite values are UTC already)."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.date()


def end_of_day(as_of: date) -> datetime:
    """Exclusive upper bound: everything strictly before this is 'on or before as_of'."""
    return datetime.combine(as_of + timedelta(days=1), time.min, tzinfo=timezone.utc)


def _month_key(first_day: date) -> str:
    return f"{first_day.year:04d}-{first_day.month:02d}"


def history_span(first_day: date, data_end: date, as_of: date) -> list[date]:
    """
    The 1st of every history month, oldest first (possibly empty).
    The last month is the latest COMPLETE month before as_of (as_of's own month
    counts only when as_of is its last day), but never later than the month of
    the latest data. Pure function, so the rule is easy to test.
    """
    if (as_of + timedelta(days=1)).day == 1:            # as_of is a month end
        last_complete = month_start(as_of)
    else:
        last_complete = month_start(month_start(as_of) - timedelta(days=1))
    last = min(last_complete, month_start(data_end))
    months: list[date] = []
    current = month_start(first_day)
    while current <= last:
        months.append(current)
        current = next_month(current)
    return months


# --- Loading rows ---

@dataclass(frozen=True)
class HistoryRow:
    """One transaction, reduced to what the forecast needs (no description: privacy by design)."""
    on: date
    amount: float
    key: str                  # recurrence_key(...)
    category: str | None


def _load_rows(db: Session, user_id: int, as_of: date, condition) -> list[HistoryRow]:
    rows = (
        db.query(Transaction.transaction_date, Transaction.amount, Transaction.payee_key,
                 Transaction.description, Transaction.category)
        .filter(Transaction.user_id == user_id, Transaction.transaction_date < end_of_day(as_of), condition)
        .all()
    )
    return [
        HistoryRow(to_utc_date(moment), float(amount), recurrence_key(payee_key, description), category)
        for moment, amount, payee_key, description, category in rows
    ]


# --- Recurring items ---

@dataclass(frozen=True)
class DetectedRecurring:
    """A recurring item plus what the forecast needs to know about it."""
    kind: str                 # KIND_INCOME | KIND_FIXED_EXPENSE | KIND_INVESTMENT
    item: RecurringItem
    category: str | None      # most common category of its regular payments
    active: bool              # False = stale (not seen recently): shown, but not projected

    def matches(self, row: HistoryRow) -> bool:
        """Is this history row one of the item's regular payments?"""
        return row.key == self.item.payee_key and is_occurrence_of(
            self.item, row.amount, settings.FORECAST_RECURRING_AMOUNT_TOLERANCE
        )


def _detect(rows: list[HistoryRow], kind: str, direction: str, data_end: date) -> list[DetectedRecurring]:
    """Runs the detector on one kind of rows (so a SIP is never mixed up with rent)."""
    minimum = settings.FORECAST_RECURRING_MIN_OCCURRENCES
    items = detect_recurring(
        (Event(r.key, r.on, r.amount, direction) for r in rows),
        min_occurrences=minimum,
        amount_tolerance=settings.FORECAST_RECURRING_AMOUNT_TOLERANCE,
        recent_occurrences=minimum,
        check_frequency=settings.FORECAST_RECURRING_FREQUENCY_CHECK,
    )
    detected: list[DetectedRecurring] = []
    for item in items:
        candidate = DetectedRecurring(kind, item, None, is_active(item, data_end, settings.FORECAST_RECURRING_STALE_DAYS))
        categories = Counter(r.category for r in rows if r.category and candidate.matches(r))
        category = categories.most_common(1)[0][0] if categories else None
        detected.append(DetectedRecurring(kind, item, category, candidate.active))
    return detected


# --- The prepared history ---

@dataclass(frozen=True)
class PreparedHistory:
    """Everything the forecast service needs from the database, as plain values."""
    as_of: date
    data_end: date                        # date of the latest transaction on/before as_of
    months: list[str]                     # history months 'YYYY-MM', oldest first
    variable_series: list[float]          # expenses minus regular recurring fixed payments, per month
    total_expense_series: list[float]     # all expenses per month (the no-decomposition ablation)
    first_forecast_month: date            # the 1st of the month right after the history
    recurring: list[DetectedRecurring] = field(default_factory=list)

    @property
    def history_months(self) -> int:
        return len(self.months)


def _not_enough_history() -> HTTPException:
    needed = settings.FORECAST_MIN_HISTORY_MONTHS
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        f"A forecast needs at least {needed} complete month(s) of expenses before the forecast date. "
        "Import older statements and try again.",
    )


@dataclass(frozen=True)
class _UserRows:
    """The user's rows up to as_of, split by kind, plus the date of the latest data."""
    data_end: date
    first_day: date                # first expense (or data_end when there is none)
    expenses: list[HistoryRow]
    investments: list[HistoryRow]
    income: list[HistoryRow]


def _load_user_rows(db: Session, user_id: int, as_of: date) -> _UserRows | None:
    """
    None when the user has no transaction on or before as_of. The history starts
    at the first EXPENSE (a month holding only a refund is not a 0-spending month);
    data_end is the latest transaction of any kind.
    """
    last_moment = (
        db.query(func.max(Transaction.transaction_date))
        .filter(Transaction.user_id == user_id, Transaction.transaction_date < end_of_day(as_of))
        .scalar()
    )
    if last_moment is None:
        return None
    expenses = _load_rows(db, user_id, as_of, EXPENSE_FILTER)
    data_end = to_utc_date(last_moment)
    return _UserRows(
        data_end=data_end,
        first_day=min((row.on for row in expenses), default=data_end),
        expenses=expenses,
        investments=_load_rows(db, user_id, as_of, INVESTMENT_OUT_FILTER),
        income=_load_rows(db, user_id, as_of, INCOME_FILTER),
    )


def _detect_all(rows: _UserRows) -> list[DetectedRecurring]:
    return (
        _detect(rows.income, KIND_INCOME, CREDIT, rows.data_end)
        + _detect(rows.expenses, KIND_FIXED_EXPENSE, DEBIT, rows.data_end)
        + _detect(rows.investments, KIND_INVESTMENT, DEBIT, rows.data_end)
    )


def detect_user_recurring(db: Session, user_id: int, as_of: date) -> list[DetectedRecurring]:
    """Recurring items only (GET /api/forecast/recurring). Needs no complete month, never raises."""
    rows = _load_user_rows(db, user_id, as_of)
    return _detect_all(rows) if rows is not None else []


def prepare_history(db: Session, user_id: int, as_of: date) -> PreparedHistory:
    """
    Reads the user's transactions up to as_of and builds the forecast inputs.
    Raises HTTP 422 when there is not enough complete-month expense history.
    """
    rows = _load_user_rows(db, user_id, as_of)
    if rows is None:
        raise _not_enough_history()
    span = history_span(rows.first_day, rows.data_end, as_of)
    recurring = _detect_all(rows)
    fixed = [r for r in recurring if r.kind == KIND_FIXED_EXPENSE]

    # Monthly totals; months with no expense inside the span stay at 0
    keys = [_month_key(m) for m in span]
    totals = dict.fromkeys(keys, 0.0)
    variable = dict.fromkeys(keys, 0.0)
    expense_rows_in_span = 0
    for row in rows.expenses:
        key = _month_key(month_start(row.on))
        if key not in totals:
            continue                      # outside the history (e.g. the current partial month)
        expense_rows_in_span += 1
        totals[key] += row.amount
        if not any(item.matches(row) for item in fixed):   # stale items too: those payments WERE fixed
            variable[key] += row.amount

    if len(span) < settings.FORECAST_MIN_HISTORY_MONTHS or expense_rows_in_span == 0:
        raise _not_enough_history()

    logger.info(
        f"Forecast history user_id={user_id} as_of={as_of} months={len(span)} data_end={rows.data_end} "
        f"recurring={len(recurring)} (fixed={len(fixed)})"
    )
    return PreparedHistory(
        as_of=as_of,
        data_end=rows.data_end,
        months=keys,
        variable_series=[round(variable[k], 2) for k in keys],
        total_expense_series=[round(totals[k], 2) for k in keys],
        first_forecast_month=next_month(span[-1]),
        recurring=recurring,
    )


# --- Inputs for the budget link ---

def variable_category_shares(db: Session, user_id: int, history: PreparedHistory, months: int) -> dict[str | None, float]:
    """
    Each category's share of VARIABLE spending over the last `months` history
    months (regular recurring payments excluded: they are projected on their own
    dates). Uncategorized spending keeps its share under None, so the shares of
    real categories are not inflated. Empty dict when there was no variable spending.
    """
    window = set(history.months[-months:])
    fixed = [r for r in history.recurring if r.kind == KIND_FIXED_EXPENSE]
    totals: dict[str | None, float] = {}
    for row in _load_rows(db, user_id, history.as_of, EXPENSE_FILTER):
        if _month_key(month_start(row.on)) in window and not any(item.matches(row) for item in fixed):
            totals[row.category] = totals.get(row.category, 0.0) + row.amount
    total = sum(totals.values())
    return {category: amount / total for category, amount in totals.items()} if total > 0 else {}


def month_to_date_spending(db: Session, user_id: int, as_of: date) -> dict[str | None, float]:
    """Expenses per category from the 1st of as_of's month up to and including as_of."""
    start = datetime.combine(month_start(as_of), time.min, tzinfo=timezone.utc)
    spent: dict[str | None, float] = {}
    rows = (
        db.query(Transaction.category, Transaction.amount)
        .filter(
            Transaction.user_id == user_id,
            Transaction.transaction_date >= start,
            Transaction.transaction_date < end_of_day(as_of),
            EXPENSE_FILTER,
        )
        .all()
    )
    for category, amount in rows:
        spent[category] = spent.get(category, 0.0) + float(amount)
    return spent


# --- Opening balance ---

@dataclass(frozen=True)
class OpeningBalance:
    amount: Decimal
    source: str               # OPENING_FROM_REQUEST | OPENING_FROM_STATEMENT
    on: date | None           # date of the statement row (None when typed by the user)


def resolve_opening_balance(db: Session, user_id: int, as_of: date, requested: Decimal | None) -> OpeningBalance:
    """
    Order of precedence: the request parameter, else the latest running balance
    printed on an imported statement (on or before as_of), else HTTP 400.
    Rows on the same day are ordered by id (the import order). The date is
    returned so a stale balance (e.g. from two months ago) is visible.
    """
    if requested is not None:
        return OpeningBalance(to_money(requested), OPENING_FROM_REQUEST, None)
    latest = (
        db.query(Transaction.balance, Transaction.transaction_date)
        .filter(
            Transaction.user_id == user_id,
            Transaction.balance.is_not(None),
            Transaction.transaction_date < end_of_day(as_of),
        )
        .order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
        .first()
    )
    if latest is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No statement balance found. Pass opening_balance (your current account balance) with the request.",
        )
    balance, moment = latest
    return OpeningBalance(to_money(balance), OPENING_FROM_STATEMENT, to_utc_date(moment))


# --- Income source ---

@dataclass(frozen=True)
class IncomeBasis:
    source: str                       # INCOME_FROM_RECURRING | INCOME_FROM_PROFILE | INCOME_NONE
    monthly_amount: Decimal           # recurring: sum of active typical amounts; profile: monthly_income


def resolve_income(db: Session, user_id: int, recurring: list[DetectedRecurring]) -> IncomeBasis:
    """
    Detected recurring income credits win (projected on their own dates). Without
    them, the Phase 4 profile monthly_income is used, and the response says so.
    With neither, income is forecast as 0 (also said in the response).
    """
    active_income = [r for r in recurring if r.kind == KIND_INCOME and r.active]
    if active_income:
        total = sum((to_money(r.item.typical_amount) for r in active_income), Decimal("0.00"))
        return IncomeBasis(INCOME_FROM_RECURRING, total)
    profile = get_profile(db, user_id)
    if profile is not None:
        return IncomeBasis(INCOME_FROM_PROFILE, to_money(profile.monthly_income))
    return IncomeBasis(INCOME_NONE, Decimal("0.00"))
