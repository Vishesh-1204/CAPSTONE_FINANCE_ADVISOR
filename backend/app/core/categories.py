# app/core/categories.py
#
# Purpose: the ONE place that defines every category label.
# The LLM prompt, the rule engine, the type detector, the API validation and
# (later) the budgeting phase all import from here, so a category can never be
# spelled two different ways in two different files.
#
# Two families of labels:
#   * EXPENSE categories (CATEGORY_GUIDE) - money going out. The LLM may only
#     choose from these.
#   * CREDIT-side labels (income + redemption) - money coming in. These are
#     always assigned by deterministic rules, never by the LLM.

# Each expense category has a short definition. These definitions go into the
# LLM prompt: telling the model what a category MEANS is the cheapest way to
# improve accuracy (e.g. gym -> Personal Care, not Healthcare).
CATEGORY_GUIDE: dict[str, str] = {
    "Food & Dining": "restaurants, cafes, food delivery apps (Swiggy, Zomato), snacks, eating out",
    "Groceries": "supermarkets, vegetables, milk, kirana stores, grocery delivery (BigBasket, Blinkit, Zepto)",
    "Transportation": "daily commute: cabs, autos, buses, metro, fuel, parking, tolls, vehicle servicing",
    "Housing & Rent": "house rent, society maintenance, home loan EMI, home repairs",
    "Utilities": "electricity, water, gas, internet, mobile and DTH recharges",
    "Healthcare": "doctors, hospitals, medicines, pharmacy, diagnostic tests, dental care",
    "Entertainment": "movies, concerts, games, events, outings, hobbies (NOT recurring subscriptions)",
    "Shopping": "clothes, shoes, electronics, gadgets, online shopping, home goods, gifts",
    "Education": "tuition, school or college fees, courses, books, exam fees",
    "Travel": "flights, trains, hotel bookings, holiday trips, tour packages, visa fees",
    "Insurance": "life, health and vehicle insurance premiums",
    "Investments & Savings": "mutual funds, SIPs, stocks, broker top-ups, PPF, NPS, fixed or recurring deposits",
    "Personal Care": "salon, spa, grooming, skincare, gym memberships",
    "Subscriptions": "recurring digital subscriptions: Netflix, Spotify, YouTube Premium, cloud storage, Prime",
    "Transfers": "money sent to a person (friends, family) or moved between your own accounts",
    "Other": "anything that clearly fits nowhere else (donations, bank charges, cash withdrawals)",
}

# Expense category names (kept under this name for code written against v2)
CATEGORY_NAMES: list[str] = list(CATEGORY_GUIDE)

# Named constants for labels that code refers to directly
INVESTMENTS_CATEGORY = "Investments & Savings"
TRANSFERS_CATEGORY = "Transfers"
INVESTMENT_REDEMPTION_CATEGORY = "Investment Redemption"   # money back from a broker/MF: NOT income
SALARY_CATEGORY = "Salary"
INTEREST_CATEGORY = "Interest"
DIVIDEND_CATEGORY = "Dividend"
REFUND_CATEGORY = "Refund & Cashback"
OTHER_INCOME_CATEGORY = "Other Income"

INCOME_CATEGORY_NAMES: list[str] = [
    SALARY_CATEGORY, INTEREST_CATEGORY, DIVIDEND_CATEGORY, REFUND_CATEGORY, OTHER_INCOME_CATEGORY,
]

# Every label a transaction may carry (what a user may pick in PATCH /category)
ALL_CATEGORY_NAMES: list[str] = CATEGORY_NAMES + INCOME_CATEGORY_NAMES + [INVESTMENT_REDEMPTION_CATEGORY]

# Used when the model returns something outside the expense list
FALLBACK_CATEGORY = "Other"

# Lowercase lookups so "food & dining" from a model or a user maps to the exact name
_CANONICAL_ALL = {name.lower(): name for name in ALL_CATEGORY_NAMES}
_CANONICAL_EXPENSE = {name.lower(): name for name in CATEGORY_NAMES}


# Which labels make sense for each direction. Transfers fits both
# (self-transfer out / in). An expense label on a credit, or "Salary" on a
# debit, is rejected so txn_type can never become nonsense.
CREDIT_CATEGORY_NAMES: list[str] = INCOME_CATEGORY_NAMES + [INVESTMENT_REDEMPTION_CATEGORY, TRANSFERS_CATEGORY]
_ALLOWED_BY_DIRECTION: dict[str, frozenset[str]] = {
    "debit": frozenset(CATEGORY_NAMES),
    "credit": frozenset(CREDIT_CATEGORY_NAMES),
}


def category_fits_direction(category: str, direction: str) -> bool:
    """True if `category` is a sensible label for a debit/credit transaction."""
    return category in _ALLOWED_BY_DIRECTION.get(direction, frozenset())


def categories_for_direction(direction: str) -> list[str]:
    """The labels a user may choose for a debit or a credit (for error messages / UI)."""
    return CATEGORY_NAMES if direction == "debit" else CREDIT_CATEGORY_NAMES


def canonical_category(value: object) -> str | None:
    """Returns the exact label for a loosely-typed value (any family), or None if it isn't one."""
    if not isinstance(value, str):
        return None
    return _CANONICAL_ALL.get(value.strip().lower())


def canonical_expense_category(value: object) -> str | None:
    """Like canonical_category, but only accepts EXPENSE categories (used for LLM output)."""
    if not isinstance(value, str):
        return None
    return _CANONICAL_EXPENSE.get(value.strip().lower())
