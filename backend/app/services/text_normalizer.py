# app/services/text_normalizer.py
#
# Purpose: bank statements are noisy. "UPI/412345678901/SWIGGY/okaxis" and
# "Swiggy" are the same merchant. This turns raw descriptions into a clean,
# comparable key so the system can recognise repeat merchants.

import re

# Payment-rail words that carry no information about WHAT was bought
_NOISE_TOKENS = frozenset({
    "upi", "imps", "neft", "rtgs", "ach", "pos", "ecom", "txn", "ref", "debit",
    # common UPI app handles that appear as separate words ("/swiggy/okaxis")
    "okaxis", "oksbi", "okhdfcbank", "okicici", "ybl", "ibl", "axl", "apl",
})

# UPI handles such as "zomato@hdfcbank" -> drop the "@hdfcbank" part
_UPI_HANDLE = re.compile(r"@[a-z0-9.\-]+")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def normalize_description(description: str) -> str:
    """
    Lowercases, strips punctuation, UPI handles, reference numbers and
    payment-rail words, and returns a compact key.

    'UPI/412345678901/SWIGGY/okaxis'  -> 'swiggy'
    'Swiggy order - dinner'           -> 'swiggy order dinner'
    """
    text = _UPI_HANDLE.sub(" ", description.lower().strip())
    tokens = _NON_ALPHANUMERIC.sub(" ", text).split()

    kept = [
        token for token in tokens
        if not token.isdigit()                                          # pure numbers: ids, amounts
        and token not in _NOISE_TOKENS
        and not (len(token) >= 8 and any(c.isdigit() for c in token))   # long ref codes like 'ab123456'
    ]
    normalized = " ".join(kept)[:200]

    # If everything was noise, fall back to the cleaned original rather than ''
    return normalized or _NON_ALPHANUMERIC.sub(" ", text).strip()[:200]
