# app/services/rule_based_categorizer.py
#
# Purpose: Layer 2 of the categorizer. A keyword dictionary that handles the
# obvious cases (Swiggy, Netflix, Uber...) instantly, for free, and the same
# way every time. Two benefits:
#   1. Fewer LLM calls (faster, cheaper, fewer rate-limit problems).
#   2. It is the RULE-BASED BASELINE you compare the LLM against in the paper.
#
# Design rule: PRECISION OVER COVERAGE. A rule only fires when the keywords
# point to exactly ONE category. If two categories match ("Swiggy Instamart"
# = food or groceries?), the rule stays silent and the LLM decides.
# Ambiguous brands (Amazon, Paytm) are deliberately left out.

import re
from dataclasses import dataclass

from app.core.merchant_dictionary import INVESTMENT_KEYWORDS
from app.services.text_normalizer import normalize_description

# Keywords are matched against the NORMALIZED description (lowercase, no punctuation)
KEYWORDS: dict[str, tuple[str, ...]] = {
    "Food & Dining": (
        "swiggy", "zomato", "domino", "dominos", "pizza", "burger", "kfc", "mcdonald", "mcdonalds",
        "starbucks", "cafe", "restaurant", "biryani", "dhaba", "haldiram", "chaayos",
    ),
    "Groceries": (
        "bigbasket", "big basket", "blinkit", "zepto", "instamart", "dmart", "d mart", "big bazaar",
        "grocery", "groceries", "supermarket", "vegetables", "kirana", "jiomart", "reliance fresh",
    ),
    "Transportation": (
        "uber", "ola", "rapido", "metro", "fastag", "petrol", "diesel", "fuel", "parking", "toll",
        "namma yatri", "auto rickshaw",
    ),
    "Housing & Rent": (
        "house rent", "monthly rent", "flat rent", "room rent", "pg rent", "rent payment",
        "society maintenance", "home loan", "housing loan",
    ),
    "Utilities": (
        "electricity", "electric bill", "water bill", "gas bill", "piped gas", "lpg", "gas cylinder",
        "broadband", "wifi", "mobile recharge", "dth", "jio", "airtel", "vodafone", "bsnl", "tata power",
    ),
    "Healthcare": (
        "pharmacy", "medplus", "apollo", "1mg", "pharmeasy", "hospital", "clinic", "doctor",
        "diagnostic", "lab test", "blood test", "dental", "dentist", "medicine", "medicines",
    ),
    "Entertainment": (
        "bookmyshow", "cinema", "pvr", "inox", "movie", "movies", "concert", "bowling", "amusement park",
    ),
    "Shopping": (
        "myntra", "flipkart", "ajio", "meesho", "shopping", "clothes", "shoes", "electronics",
    ),
    "Education": (
        "tuition", "udemy", "coursera", "byju", "unacademy", "school fee", "college fee",
        "semester fee", "exam fee", "course fee", "coaching",
    ),
    "Travel": (
        "makemytrip", "goibibo", "cleartrip", "redbus", "irctc", "flight", "flights", "airline",
        "indigo", "air india", "oyo", "airbnb", "hotel booking", "train ticket", "tour package", "visa",
    ),
    "Insurance": (
        "insurance", "policybazaar", "lic premium", "lic of india", "star health",
    ),
    # Shared with the txn_type detector, so both always agree on what an investment is
    "Investments & Savings": INVESTMENT_KEYWORDS,
    "Personal Care": (
        "salon", "haircut", "spa", "barber", "grooming", "skincare", "gym", "parlour", "massage", "nykaa",
    ),
    "Subscriptions": (
        "netflix", "spotify", "hotstar", "prime video", "amazon prime", "youtube premium", "icloud",
        "google one", "apple music", "disney", "sonyliv", "zee5", "chatgpt",
    ),
}

# Compile once at import time. \b means "word boundary", so 'ola' matches
# 'ola cab' but NOT 'cola', and 'spa' does not match 'space'.
_PATTERNS: dict[str, re.Pattern[str]] = {
    category: re.compile(r"\b(?:" + "|".join(re.escape(k) for k in sorted(words, key=len, reverse=True)) + r")\b")
    for category, words in KEYWORDS.items()
}

RULE_CONFIDENCE = 0.95


@dataclass(frozen=True)
class RuleMatch:
    category: str
    keyword: str


def has_any_keyword(description: str) -> bool:
    """True if ANY category keyword appears (even an ambiguous multi-category match)."""
    text = normalize_description(description)
    return any(pattern.search(text) for pattern in _PATTERNS.values())


def rule_categorize(description: str) -> RuleMatch | None:
    """
    Returns a RuleMatch if the description points to exactly one category,
    otherwise None (no match, or an ambiguous match that the LLM should decide).
    """
    text = normalize_description(description)

    matches: list[RuleMatch] = []
    for category, pattern in _PATTERNS.items():
        found = pattern.search(text)
        if found:
            matches.append(RuleMatch(category=category, keyword=found.group(0)))

    return matches[0] if len(matches) == 1 else None
