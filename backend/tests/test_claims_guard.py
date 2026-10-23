# tests/test_claims_guard.py

import pytest

from app.services.investing.claims_guard import find_prohibited_claims, is_compliant


@pytest.mark.parametrize("text", [
    "This fund gives guaranteed returns.", "A risk-free way to grow money.", "You can't lose with an index fund.",
    "It will definitely beat inflation.", "Assured returns of 12%.", "A sure-shot plan.", "Zero risk investment.",
])
def test_promises_are_caught(text):
    assert not is_compliant(text)


def test_balanced_language_passes():
    assert is_compliant("Equity can fall sharply in the short term, so this plan keeps a debt cushion.")


def test_matches_are_reported():
    assert find_prohibited_claims("Guaranteed gains and zero risk.") == ["guaranteed", "zero risk"]


def test_even_the_negated_word_guarantee_is_flagged_on_purpose():
    # Known, deliberate over-strictness: negations are flagged too (fail safe -> template text is used).
    assert not is_compliant("The plan is not a guarantee of results; markets move.")


@pytest.mark.parametrize("text", ["You can\u2019t lose with this.", "You can't lose with this.", "You cant lose with this."])
def test_curly_straight_and_missing_apostrophes_are_all_caught(text):
    assert not is_compliant(text)
