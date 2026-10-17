# tests/test_txn_type_detector.py
#
# Every investment and income case from spec section 6, plus the money helper.
# All narrations are synthetic.

from decimal import Decimal

import pytest

from app.core.categories import ALL_CATEGORY_NAMES, CATEGORY_NAMES
from app.core.money import to_money
from app.services.narration_parser import parse_narration
from app.services.txn_type_detector import detect_txn_type


def detect(narration: str, direction: str):
    return detect_txn_type(narration, direction, parse_narration(narration).channel)


@pytest.mark.parametrize("narration, subtype", [
    ("NACH/HDFC MF SIP/0012345", "sip"),
    ("ACH D- ICICI PRU MF-1234567", "sip"),                    # mandate channel => SIP
    ("UPI/412345678901/GROWW SIP/groww.sip@axisbank", "sip"),
    ("UPI/412345678901/ZERODHA/zerodha.broking@hdfcbank", "wallet_topup"),
    ("IMPS/UPSTOX SECURITIES/ADD FUNDS", "wallet_topup"),
    ("Angel One fund transfer", "wallet_topup"),
    ("INDMONEY US STOCKS", "wallet_topup"),
    ("BSE STAR MF purchase", "lump_sum"),
    ("PPF deposit FY26", "lump_sum"),
    ("Fixed deposit booking", "lump_sum"),
])
def test_investment_outflows(narration, subtype):
    result = detect(narration, "debit")
    assert result.txn_type == "investment_out"
    assert result.investment_subtype == subtype
    assert result.category == "Investments & Savings"


@pytest.mark.parametrize("narration, txn_type, category", [
    ("NEFT-ACME TECH PAYROLL SEP", "income", "Salary"),
    ("SALARY CREDIT SEP 2026", "income", "Salary"),
    ("SB INT PD 01-07-2026 TO 30-09-2026", "income", "Interest"),
    ("FD interest credit", "income", "Interest"),
    ("ACH C- INFOSYS DIVIDEND 2026", "income", "Dividend"),
    ("Swiggy refund order 1234", "income", "Refund & Cashback"),
    ("Cashback from card offer", "income", "Refund & Cashback"),
    ("ZERODHA BROKING WITHDRAWAL", "investment_in", "Investment Redemption"),
    ("NIPPON INDIA MF REDEMPTION", "investment_in", "Investment Redemption"),
    ("Groww SIP redeem", "investment_in", "Investment Redemption"),
    ("UPI/412345678901/GROWW/groww.payout@axisbank", "investment_in", "Investment Redemption"),
    ("Self transfer from SBI", "transfer", "Transfers"),
])
def test_credits_are_always_labelled(narration, txn_type, category):
    result = detect(narration, "credit")
    assert (result.txn_type, result.category) == (txn_type, category)
    assert result.confidence >= 0.9


def test_unknown_credit_is_other_income_with_low_confidence():
    result = detect("NEFT-XYZ ENTERPRISES-4567", "credit")
    assert (result.txn_type, result.category) == ("income", "Other Income")
    assert result.confidence < 0.6                     # lands in the review queue


@pytest.mark.parametrize("narration", ["Swiggy order", "UPI/412345678901/BIGBASKET/okaxis", "Monthly house rent"])
def test_ordinary_debits_are_expenses_with_no_category_yet(narration):
    result = detect(narration, "debit")
    assert result.txn_type == "expense" and result.category is None


def test_self_transfer_debit_is_not_an_expense():
    result = detect("IMPS to own account HDFC", "debit")
    assert (result.txn_type, result.category) == ("transfer", "Transfers")


def test_category_lists_are_consistent():
    assert "Transfers" in CATEGORY_NAMES
    assert "Salary" not in CATEGORY_NAMES                   # the LLM may only pick expenses
    assert len(ALL_CATEGORY_NAMES) == len(set(ALL_CATEGORY_NAMES))
    assert {"Salary", "Interest", "Dividend", "Refund & Cashback", "Other Income", "Investment Redemption"} <= set(ALL_CATEGORY_NAMES)


@pytest.mark.parametrize("raw, expected", [
    (99.1, Decimal("99.10")), ("1234.567", Decimal("1234.57")), (0.1 + 0.2, Decimal("0.30")), (5, Decimal("5.00")),
])
def test_to_money_is_exact(raw, expected):
    assert to_money(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "", "NaN", "inf"])
def test_to_money_rejects_non_numbers(raw):
    with pytest.raises(ValueError):
        to_money(raw)
