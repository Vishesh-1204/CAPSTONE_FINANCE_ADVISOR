# tests/test_explanation_guard.py
#
# Unit tests for the faithfulness guard: the LLM may only mention rupee
# amounts that are in the plan.

from decimal import Decimal

from app.agents.explanation_guard import extract_rupee_amounts, find_unsupported_amounts, is_faithful


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
