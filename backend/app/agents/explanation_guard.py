# app/agents/explanation_guard.py
#
# Purpose: the LLM is only allowed to EXPLAIN numbers the rules engine already
# produced. This module checks that an LLM-written explanation does not contain
# a rupee amount that is not in the plan. If it does, the caller discards the
# LLM text and uses the deterministic template instead. It is also a measurable
# "faithfulness" metric for the research paper (how often does the LLM invent a number?).
# Phase 6 adds the same check for percentages, and a detector for lakh / crore / k / L
# amounts, which the rupee pattern would otherwise mis-read.
#
# It lives in agents/ (not services/) because the planner agent calls it, and
# agents must not import services. Pure functions: no DB, no HTTP, no LLM.

import re
from collections.abc import Iterable
from decimal import Decimal

# Matches rupee amounts such as ₹4,170 / ₹ 4170.50 / Rs. 9,000 / INR 1,200 / ₹1,00,000.
# \b stops words that merely END in "rs" ("yours 500", "hours 3") from counting as rupees.
_AMOUNT_PATTERN = re.compile(r"(?:₹|\brs\.?|\binr)\s*([0-9][0-9,]*(?:\.[0-9]+)?)", re.IGNORECASE)


def extract_rupee_amounts(text: str) -> list[float]:
    """Returns every rupee amount mentioned in `text` as a float."""
    amounts: list[float] = []
    for raw in _AMOUNT_PATTERN.findall(text):
        # A full stop after the number ("₹9,000.") is never captured, because
        # the decimal part needs at least one digit; only the commas go.
        cleaned = raw.replace(",", "")
        if cleaned:
            amounts.append(float(cleaned))
    return amounts


def find_unsupported_amounts(
    text: str, allowed: Iterable[float | Decimal], tolerance: float = 1.0
) -> list[float]:
    """
    Returns the amounts in `text` that are NOT within `tolerance` rupees of any
    allowed number. The tolerance lets the LLM round (4170.4 -> 4,170).
    """
    allowed_values = [float(value) for value in allowed]   # plan numbers may be Decimal
    unsupported: list[float] = []
    for amount in extract_rupee_amounts(text):
        if not any(abs(amount - value) <= tolerance for value in allowed_values):
            unsupported.append(amount)
    return unsupported


def is_faithful(text: str, allowed: Iterable[float | Decimal], tolerance: float = 1.0) -> bool:
    """True when the explanation mentions no amount outside the plan's numbers."""
    return not find_unsupported_amounts(text, allowed, tolerance)


# --- Percentages and amount shorthand (added for the Investment Advisor, Phase 6) ---

# 55% / 9.5 % / 20 percent / 20 per cent. "10th percentile" does not match (no digit right before "per").
_PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per\s?cent\b)", re.IGNORECASE)

# Indian and abbreviated units: "₹12.5 lakh" would be read by the rupee guard as ₹12.5,
# so such text is rejected outright instead of being checked wrongly.
_SHORTHAND_PATTERN = re.compile(r"\b(?:lakhs?|lacs?|crores?)\b|\d\s*(?:k|l|cr)\b", re.IGNORECASE)


def extract_percentages(text: str) -> list[float]:
    """Returns every percentage mentioned in `text` as a number (55% -> 55.0)."""
    return [float(raw) for raw in _PERCENT_PATTERN.findall(text)]


def find_unsupported_percentages(
    text: str, allowed: Iterable[float | Decimal], tolerance: float = 1.0
) -> list[float]:
    """Percentages in `text` more than `tolerance` percentage points from every allowed one."""
    allowed_values = [float(value) for value in allowed]
    return [
        value for value in extract_percentages(text)
        if not any(abs(value - allowed_value) <= tolerance for allowed_value in allowed_values)
    ]


def find_amount_shorthand(text: str) -> list[str]:
    """Every 'lakh' / 'crore' / '12k' / '5L' / '1.2 Cr' style amount, lower-cased."""
    return [match.group(0).lower() for match in _SHORTHAND_PATTERN.finditer(text)]
