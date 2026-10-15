# tests/test_text_normalizer.py

import pytest

from app.services.text_normalizer import normalize_description


@pytest.mark.parametrize("raw, expected", [
    ("UPI/412345678901/SWIGGY/okaxis", "swiggy"),
    ("UPI-ZOMATO-ZOMATO@HDFCBANK-512345678902", "zomato zomato"),
    ("IMPS/BIGBASKET/AB123456", "bigbasket"),
    ("POS 4829 UBER INDIA", "uber india"),
    ("Swiggy order - dinner", "swiggy order dinner"),
    ("  Monthly   HOUSE rent ", "monthly house rent"),
])
def test_noise_is_removed(raw, expected):
    assert normalize_description(raw) == expected


def test_same_merchant_different_noise_gives_same_key():
    assert normalize_description("UPI/111111111111/NETFLIX") == normalize_description("UPI/222222222222/netflix")


def test_all_noise_falls_back_to_cleaned_original():
    assert normalize_description("12345") == "12345"


def test_empty_input_does_not_crash():
    assert normalize_description("   ") == ""
