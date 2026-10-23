# tests/test_explanation_guard.py
#
# Unit tests for the faithfulness guard: the LLM may only mention rupee
# amounts that are in the plan.

from decimal import Decimal

import pytest

from app.agents.explanation_guard import (
    extract_percentages, extract_rupee_amounts, find_amount_shorthand, find_unsupported_amounts,
    find_unsupported_percentages, is_faithful,
)


def test_extracts_amounts_in_different_formats():
    text = "Spend ₹4,170 on food, Rs. 9000 on rent and INR 1,200.50 on fuel."
    assert extract_rupee_amounts(text) == [4170.0, 9000.0, 1200.5]


def test_numbers_without_a_currency_marker_are_ignored():
    assert extract_rupee_amounts("You have 3 months of data and a 10% trim.") == []


def test_faithful_when_all_amounts_come_from_the_plan():
    assert is_faithful("Food is capped at ₹4,170.", allowed=[4170.0, 9000.0])


def test_rounding_within_tolerance_is_allowed():
    assert is_faithful("Food is capped at ₹4,170.", allowed=[4170.4])


def test_invented_amount_is_caught():
    bad = find_unsupported_amounts("Cut dining to ₹2,500 and save ₹4,170.", allowed=[4170.0])
    assert bad == [2500.0]


def test_trailing_full_stop_is_not_part_of_the_number():
    assert extract_rupee_amounts("Rent is ₹9,000.") == [9000.0]


# --- additions ---

def test_indian_lakh_grouping_is_read_correctly():
    assert extract_rupee_amounts("Your income is ₹1,00,000 a month.") == [100000.0]


def test_words_ending_in_rs_are_not_rupees():
    # Without the \b word boundary, "yours 500" and "hours 3" would be read as ₹500 and ₹3
    assert extract_rupee_amounts("Keep yours 500 times better, it takes hours 3 days.") == []


def test_decimal_allowed_values_are_supported():
    assert is_faithful("Groceries: ₹3,333.33.", allowed=[Decimal("3333.33")])


def test_tolerance_is_configurable():
    assert not is_faithful("Food is capped at ₹4,175.", allowed=[4170.0], tolerance=1.0)
    assert is_faithful("Food is capped at ₹4,175.", allowed=[4170.0], tolerance=5.0)


# --- Phase 6: Indian digit grouping, percentages, lakh / crore shorthand ---

def test_indian_digit_grouping_is_read_as_one_amount():
    assert extract_rupee_amounts("A range of ₹12,34,567 to Rs 1,00,00,000.") == [1234567.0, 10000000.0]
    assert is_faithful("Middle outcome ₹12,34,567.", allowed=[1234567.4])


def test_extracts_percentages_in_different_formats():
    text = "Equity 55%, debt 30 %, a 62.5 percent chance, gold 10 per cent; the 10th percentile is lower."
    assert extract_percentages(text) == [55.0, 30.0, 62.5, 10.0]


def test_percentages_must_be_within_one_point_of_an_allowed_one():
    allowed = [55.0, 62.0]
    assert find_unsupported_percentages("55% equity, a 62.4% chance", allowed) == []
    assert find_unsupported_percentages("70% equity", allowed) == [70.0]


@pytest.mark.parametrize("text", [
    "about 12.3 lakh", "₹12L", "₹6k a month", "1.2 crore", "₹1.5 Cr", "5 lakhs", "Rs 3 lac",
])
def test_lakh_crore_and_suffix_amounts_are_detected(text):
    assert find_amount_shorthand(text)


def test_ordinary_words_are_not_mistaken_for_shorthand():
    assert find_amount_shorthand("Keep 3 lines of credit, 12 kg of rice, the 10th percentile, ₹12,34,567.") == []
