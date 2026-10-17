# tests/test_narration_parser.py
#
# Tests extraction of channel, UPI ID and payee key from raw narrations.
# All narrations are synthetic.

import pytest

from app.services.narration_parser import detect_channel, extract_upi_id, parse_narration


@pytest.mark.parametrize("narration, expected", [
    ("UPI/412345678901/SWIGGY/swiggy@icici", "swiggy@icici"),
    ("UPI-RAHUL KUMAR-rahul.k99@okhdfcbank-412345678901", "rahul.k99@okhdfcbank"),
    ("upi/zerodha.broking@hdfcbank/add funds", "zerodha.broking@hdfcbank"),
    ("NEFT-HDFC0001234-ACME PAYROLL", None),
    ("Refund mail from help@shop.com", None),          # e-mail address, not a UPI ID
])
def test_extract_upi_id(narration, expected):
    assert extract_upi_id(narration) == expected


@pytest.mark.parametrize("narration, channel", [
    ("UPI/412345678901/SWIGGY/okaxis", "upi"),
    ("ACH D- HDFC MF SIP-1234567", "nach"),
    ("NACH/ICICI PRU MF/SIP", "nach"),
    ("IMPS/P2A/412345678901/RENT", "imps"),
    ("NEFT-ACME PAYROLL SEP", "neft"),
    ("ATW-512345XXXXXX1234-MG ROAD", "atm"),
    ("Cash withdrawal at branch", "atm"),
    ("POS 4829 UBER INDIA", "card"),
    ("CHQ DEP 000123", "cheque"),
    ("Monthly house rent", "other"),
])
def test_detect_channel(narration, channel):
    assert detect_channel(narration, extract_upi_id(narration)) == channel


def test_payee_key_prefers_upi_id_and_falls_back_to_merchant_key():
    with_upi = parse_narration("UPI/412345678901/SWIGGY/swiggy@icici")
    assert with_upi.payee_key == "swiggy@icici" and with_upi.channel == "upi"

    without_upi = parse_narration("POS 4829 UBER INDIA")
    assert without_upi.upi_id is None and without_upi.payee_key == without_upi.description_key == "uber india"
