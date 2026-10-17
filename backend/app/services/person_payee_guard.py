# app/services/person_payee_guard.py
#
# Purpose: PRIVACY GUARD. Decides, locally, whether a debit looks like a
# payment to a PERSON ("UPI/.../RAHUL KUMAR/9876543210@ybl") rather than to a
# merchant. Such rows are labelled `Transfers` + needs_review and are NEVER
# sent to the LLM, so personal names and phone numbers stay on our server.
#
# Design choice: when unsure, prefer "person". A false positive only costs the
# user one click in the review queue; a false negative leaks a name to a
# third-party API. (Measure both in the evaluation: guard precision/recall and
# the "privacy leakage" ablation with the guard switched off.)
#
# Scope: only P2P-capable rails (UPI, IMPS, NEFT). Manually typed descriptions
# ("Flat payment Rahul") use channel 'other' and are not guarded; that is a
# documented limitation (the user chose to type that text).

import re

from app.core.transaction_types import CHANNEL_IMPS, CHANNEL_NEFT, CHANNEL_UPI
from app.services.narration_parser import extract_upi_id
from app.services.rule_based_categorizer import has_any_keyword
from app.services.txn_type_detector import mentions_investment_platform

P2P_CHANNELS = frozenset({CHANNEL_UPI, CHANNEL_IMPS, CHANNEL_NEFT})

# Words that mean "this is a business", so the payee is a merchant
BUSINESS_WORDS = frozenset({
    "pvt", "private", "ltd", "limited", "llp", "inc", "corp", "corporation", "company", "co",
    "store", "stores", "mart", "shop", "shoppe", "enterprises", "enterprise", "traders", "trading",
    "services", "service", "solutions", "technologies", "tech", "foods", "food", "restaurant",
    "hotel", "cafe", "bakery", "bakers", "sweets", "dairy", "medical", "medicals", "pharma",
    "mess", "canteen", "tiffin", "dhaba", "bhojanalaya", "caterers", "juice", "tea", "stall",
    "chemist", "chemists", "agency", "agencies", "bazaar", "india", "industries", "motors",
    "electronics", "clinic", "hospital", "school", "college", "academy", "institute", "bank",
    "payments", "retail", "fashion", "garments", "textiles", "jewellers", "kirana", "general",
    "provision", "provisions", "studio", "works", "centre", "center", "petroleum", "fuels",
    "travels", "tours", "salon", "ventures", "international", "global", "systems", "labs",
    "merchant", "biller", "billdesk", "razorpay", "cashfree", "payu", "phonepe", "paytm", "gpay",
})

# Narration filler that is neither a name nor a business word
_FILLER = frozenset({
    "p2a", "p2p", "payment", "paid", "pay", "to", "from", "transfer", "trf", "tfr", "by", "sent",
    "mr", "mrs", "ms", "dr", "shri", "smt", "mob", "note", "for", "upi", "imps", "neft", "rtgs",
})

_ALPHA_TOKEN = re.compile(r"[a-z]+")
_MOBILE_NUMBER = re.compile(r"^(?:91)?[6-9]\d{9}$")
# Merchant-style UPI IDs: Paytm/BharatPe/PhonePe QR codes, payment gateways, business handles
_MERCHANT_UPI_LOCAL = re.compile(r"^(?:paytmqr|bharatpe|q\d|gpay-|rzp|razorpay|cf\.|payu|billdesk|merchant)")
_MAX_NAME_TOKENS = 3


def _name_tokens(description: str, upi_id: str | None) -> list[str]:
    """Alphabetic words of the narration minus filler, UPI handle and duplicates."""
    text = description.lower()
    if upi_id:
        text = text.replace(upi_id, " ")
    tokens: list[str] = []
    for token in _ALPHA_TOKEN.findall(text):
        if len(token) >= 2 and token not in _FILLER and token not in tokens:
            tokens.append(token)
    return tokens


def looks_like_person(description: str, channel: str | None) -> bool:
    """
    True if this narration is probably a payment to a private person.
    Only P2P rails are considered; merchant keywords and business words win.
    """
    if channel not in P2P_CHANNELS:
        return False
    if has_any_keyword(description) or mentions_investment_platform(description):
        return False

    upi_id = extract_upi_id(description)
    tokens = _name_tokens(description, upi_id)
    if any(token in BUSINESS_WORDS for token in tokens):
        return False

    if upi_id:
        local_part = upi_id.split("@", 1)[0]
        if _MERCHANT_UPI_LOCAL.match(local_part) or "biz" in upi_id.split("@", 1)[1]:
            return False
        if _MOBILE_NUMBER.match(local_part):
            return True                       # phone-number UPI IDs belong to people

    # One to three plain words and nothing business-like: most likely a name
    return 1 <= len(tokens) <= _MAX_NAME_TOKENS
