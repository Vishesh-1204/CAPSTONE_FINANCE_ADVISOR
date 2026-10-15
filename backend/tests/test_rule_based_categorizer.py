# tests/test_rule_based_categorizer.py

import pytest

from app.core.categories import CATEGORY_NAMES
from app.services.rule_based_categorizer import KEYWORDS, rule_categorize


@pytest.mark.parametrize("description, expected", [
    ("Swiggy order - dinner", "Food & Dining"),
    ("UPI/412345678901/SWIGGY/okaxis", "Food & Dining"),
    ("Ola cab to office", "Transportation"),
    ("Netflix monthly subscription", "Subscriptions"),
    ("Zerodha mutual fund SIP", "Investments & Savings"),
    ("NEFT-LIC OF INDIA-PREMIUM", "Insurance"),
    ("Monthly house rent", "Housing & Rent"),
    ("Hotel booking Goa", "Travel"),
])
def test_clear_cases_are_matched(description, expected):
    assert rule_categorize(description).category == expected


@pytest.mark.parametrize("description", [
    "Swiggy Instamart order",            # food AND groceries -> ambiguous, leave to the LLM
    "Amazon order",                      # Amazon is deliberately not a keyword
    "Donation to temple",                # nothing matches
    "Cola and chips",                    # 'ola' must not match inside 'cola'
    "Space rental",                      # 'spa' must not match inside 'space'
    "UPI-HOTEL SAGAR RESTAURANT-1",      # 'hotel' alone is not a Travel keyword (Indian 'hotel' = restaurant)
])
def test_ambiguous_or_unknown_returns_none_or_safe_answer(description):
    match = rule_categorize(description)
    assert match is None or match.category == "Food & Dining"


def test_ambiguous_and_unknown_really_return_none():
    for description in ["Swiggy Instamart order", "Amazon order", "Donation to temple", "Cola and chips", "Space rental"]:
        assert rule_categorize(description) is None


def test_every_rule_category_is_a_real_category():
    assert set(KEYWORDS) <= set(CATEGORY_NAMES)
