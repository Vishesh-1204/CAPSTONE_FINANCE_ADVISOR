# app/services/narration_parser.py
#
# Purpose: reads a RAW bank narration and pulls out the structured facts the
# rest of the system needs, 100% locally (nothing here talks to an LLM):
#
#   "UPI/412345678901/SWIGGY/swiggy@icici"
#     -> channel='upi', upi_id='swiggy@icici', description_key='swiggy',
#        payee_key='swiggy@icici'
#
# Why a separate step from text_normalizer? The normalizer THROWS AWAY the
# UPI handle and rail words (good for matching merchants), but we need them
# first: the UPI ID is the most reliable payee identity, and the channel
# (NACH, ATM, card...) helps detect SIPs and cash withdrawals.

import re
from dataclasses import dataclass

from app.core.transaction_types import (
    CHANNEL_ATM, CHANNEL_CARD, CHANNEL_CHEQUE, CHANNEL_IMPS, CHANNEL_NACH, CHANNEL_NEFT, CHANNEL_OTHER,
    CHANNEL_UPI,
)
from app.services.text_normalizer import normalize_description

# A UPI ID is "name@handle", e.g. rahul.s@okhdfcbank. '-' is deliberately NOT
# allowed in the name: banks use it as a field separator ("UPI-RAHUL-rahul@ok..").
# The negative lookahead skips e-mail addresses ("a@gmail.com": the handle is
# followed by ".com").
_UPI_ID = re.compile(r"(?<![a-z0-9._])([a-z0-9][a-z0-9._]{1,63}@[a-z][a-z0-9]{1,30})(?![a-z0-9]|\.[a-z])")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

# Checked in this order; the first channel whose tokens appear wins.
# A UPI ID in the text is decisive on its own (checked before this table).
_CHANNEL_TOKENS: tuple[tuple[str, frozenset[str]], ...] = (
    (CHANNEL_UPI, frozenset({"upi"})),
    (CHANNEL_NACH, frozenset({"nach", "ach", "ecs", "mandate"})),
    (CHANNEL_IMPS, frozenset({"imps"})),
    (CHANNEL_NEFT, frozenset({"neft"})),
    (CHANNEL_ATM, frozenset({"atm", "atw", "nwd"})),
    (CHANNEL_CARD, frozenset({"pos", "vps", "ecom", "card"})),
    (CHANNEL_CHEQUE, frozenset({"chq", "cheque", "clg"})),
)


@dataclass(frozen=True)
class NarrationInfo:
    """Structured facts extracted from one raw narration."""
    channel: str               # one of transaction_types.CHANNELS
    upi_id: str | None         # lowercased "name@handle", if the narration has one
    description_key: str       # normalized merchant text (see text_normalizer)
    payee_key: str             # upi_id if present, otherwise description_key


def extract_upi_id(description: str) -> str | None:
    """Returns the first UPI ID in the narration (lowercased), or None."""
    match = _UPI_ID.search(description.lower())
    return match.group(1) if match else None


def detect_channel(description: str, upi_id: str | None = None) -> str:
    """Which payment rail the narration describes ('upi', 'neft', ... or 'other')."""
    if upi_id:
        return CHANNEL_UPI
    tokens = set(_TOKEN_SPLIT.split(description.lower()))
    for channel, words in _CHANNEL_TOKENS:
        if tokens & words:
            return channel
    if "cash withdrawal" in description.lower():
        return CHANNEL_ATM
    return CHANNEL_OTHER


def parse_narration(description: str) -> NarrationInfo:
    """Extracts channel, UPI ID, merchant key and payee key from a raw narration."""
    upi_id = extract_upi_id(description)
    description_key = normalize_description(description)
    return NarrationInfo(
        channel=detect_channel(description, upi_id),
        upi_id=upi_id,
        description_key=description_key,
        payee_key=(upi_id or description_key)[:200],
    )
