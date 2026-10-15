# app/core/categories.py
#
# Purpose: the ONE place that defines the expense categories.
# The LLM prompt, the rule engine, the API validation and (later) the
# budgeting phase all import from here, so a category can never be
# spelled two different ways in two different files.

# Each category has a short definition. These definitions go into the LLM
# prompt: telling the model what a category MEANS is the cheapest way to
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
    "Investments & Savings": "mutual funds, SIPs, stocks, PPF, NPS, fixed or recurring deposits",
    "Personal Care": "salon, spa, grooming, skincare, gym memberships",
    "Subscriptions": "recurring digital subscriptions: Netflix, Spotify, YouTube Premium, cloud storage, Prime",
    "Other": "anything that clearly fits nowhere else (donations, bank charges, cash withdrawals)",
}

CATEGORY_NAMES: list[str] = list(CATEGORY_GUIDE)

# Used when the model returns something outside the list
FALLBACK_CATEGORY = "Other"

# Lowercase lookup so "food & dining" from a model or a user maps to the exact name
_CANONICAL = {name.lower(): name for name in CATEGORY_NAMES}


def canonical_category(value: object) -> str | None:
    """Returns the exact category name for a loosely-typed value, or None if it isn't one."""
    if not isinstance(value, str):
        return None
    return _CANONICAL.get(value.strip().lower())
