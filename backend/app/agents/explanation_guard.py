# app/agents/explanation_guard.py
#
# Purpose: the LLM is only allowed to EXPLAIN numbers the rules engine already
# produced. This module checks that an LLM-written explanation does not contain
# a rupee amount that is not in the plan. If it does, the caller discards the
# LLM text and uses the deterministic template instead. It is also a measurable
# "faithfulness" metric for the research paper (how often does the LLM invent a number?).
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
