# app/core/stock_symbols.py
#
# Purpose: ONE strict rule for what a stock symbol may look like (Phase 7, spec section 5.6).
# A symbol ends up in provider URLs, database queries and log lines, so raw user text is
# never trusted: it is trimmed, upper-cased and checked against a whitelist pattern first.
# Lives in core/ so schemas, routes, services and market_data/ adapters all share it.
#
# Allowed: letters, digits, dot, dash, and a caret only as the FIRST character (index
# symbols such as ^NSEI). At most 20 characters. Examples: RELIANCE.NS, TCS.BSE, ^NSEI, M-M.NS

import re

MAX_SYMBOL_LENGTH = 20

# Optional leading caret, then a letter or digit, then letters / digits / dots / dashes.
# Checked AFTER upper-casing, so only A-Z is needed. fullmatch() is used, so no anchors.
_SYMBOL_PATTERN = re.compile(r"\^?[A-Z0-9][A-Z0-9.\-]*")


def normalize_symbol(raw: str) -> str:
    """
    Return the canonical symbol (trimmed, upper case) or raise ValueError.

    Upper-casing means "reliance.ns" and "RELIANCE.NS" are the same watchlist entry
    and the same cached price series.
    """
    if not isinstance(raw, str):
        raise ValueError("symbol must be text")
    symbol = raw.strip()
    # Reject non-ASCII BEFORE upper-casing: Python maps some non-ASCII letters to ASCII ones
    # ("ı".upper() == "I", "ſ".upper() == "S"), which would silently turn odd text into a real symbol.
    if not symbol.isascii():
        raise ValueError("symbol may contain only ASCII letters, digits, '.', '-' and a leading '^'")
    symbol = symbol.upper()
    if not symbol:
        raise ValueError("symbol is empty")
    if len(symbol) > MAX_SYMBOL_LENGTH:
        raise ValueError(f"symbol is longer than {MAX_SYMBOL_LENGTH} characters")
    if not _SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError(
            "symbol may contain only letters, digits, '.', '-' and a leading '^' (e.g. RELIANCE.NS, ^NSEI)"
        )
    return symbol
