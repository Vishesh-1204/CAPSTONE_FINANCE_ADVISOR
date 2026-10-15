# app/services/txn_type_detector.py
#
# Purpose: decides WHAT KIND of money movement a transaction is, before any
# expense categorization happens:
#
#   debit  + "NACH/HDFC MF SIP"        -> investment_out (sip), Investments & Savings
#   debit  + "UPI/ZERODHA"             -> investment_out (wallet_topup)
#   credit + "GROWW WITHDRAWAL"        -> investment_in, Investment Redemption (NOT income)
#   credit + "SB INT PD"               -> income, Interest
#   debit  + "Swiggy order"            -> expense (category decided later by memory/rules/LLM)
#
# Why it matters: a SIP is not spending. If investments were counted as
# expenses, the Budget agent (Phase 4) would tell a disciplined investor they
# overspend. This is deterministic and local; it never calls the LLM.

import re
from dataclasses import dataclass

from app.core import merchant_dictionary as words
from app.core.categories import (
    DIVIDEND_CATEGORY, INTEREST_CATEGORY, INVESTMENT_REDEMPTION_CATEGORY, INVESTMENTS_CATEGORY,
    OTHER_INCOME_CATEGORY, REFUND_CATEGORY, SALARY_CATEGORY, TRANSFERS_CATEGORY,
)
from app.core.transaction_types import (
    CHANNEL_NACH, CREDIT, SUBTYPE_LUMP_SUM, SUBTYPE_SIP, SUBTYPE_WALLET_TOPUP, TXN_EXPENSE, TXN_INCOME,
    TXN_INVESTMENT_IN, TXN_INVESTMENT_OUT, TXN_TRANSFER,
)
from app.services.text_normalizer import normalize_description

# Confidence for dictionary decisions (same level as keyword rules), and a low
# one for "money came in but we don't know why" so it lands in the review queue.
DETECTION_CONFIDENCE = 0.95
UNKNOWN_CREDIT_CONFIDENCE = 0.5


def _compile(keywords: tuple[str, ...]) -> re.Pattern[str]:
    """One word-boundary regex for a keyword list; longest first so 'sip redeem' beats 'sip'."""
    ordered = sorted(keywords, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(k) for k in ordered) + r")\b")


_INVESTMENT = _compile(words.INVESTMENT_KEYWORDS)
_BROKER = _compile(words.BROKER_KEYWORDS)
_MUTUAL_FUND = _compile(words.MUTUAL_FUND_KEYWORDS)
_SIP = _compile(words.SIP_KEYWORDS)
_REDEMPTION = _compile(words.REDEMPTION_KEYWORDS)
_SELF_TRANSFER = _compile(words.SELF_TRANSFER_KEYWORDS)
_SALARY = _compile(words.SALARY_KEYWORDS)
_INTEREST = _compile(words.INTEREST_KEYWORDS)
_DIVIDEND = _compile(words.DIVIDEND_KEYWORDS)
_REFUND = _compile(words.REFUND_KEYWORDS)


@dataclass(frozen=True)
class TypeDetection:
    """
    The detector's verdict. `category` is None for ordinary expenses: those are
    categorized afterwards by memory -> rules -> LLM.
    """
    txn_type: str
    investment_subtype: str | None = None
    category: str | None = None
    confidence: float = DETECTION_CONFIDENCE
    keyword: str | None = None          # what triggered it (shown as reasoning)


def _investment_subtype(text: str, channel: str | None) -> str:
    """sip (mandate / NACH), wallet_topup (money into a broker), or lump_sum."""
    if _SIP.search(text) or channel == CHANNEL_NACH:
        return SUBTYPE_SIP
    if _BROKER.search(text) and not _MUTUAL_FUND.search(text):
        return SUBTYPE_WALLET_TOPUP
    return SUBTYPE_LUMP_SUM


def _detect_debit(text: str, channel: str | None) -> TypeDetection:
    if found := _SELF_TRANSFER.search(text):
        return TypeDetection(TXN_TRANSFER, category=TRANSFERS_CATEGORY, keyword=found.group(0))
    if found := _INVESTMENT.search(text):
        return TypeDetection(
            TXN_INVESTMENT_OUT, _investment_subtype(text, channel), INVESTMENTS_CATEGORY, keyword=found.group(0)
        )
    return TypeDetection(TXN_EXPENSE)


def _detect_credit(text: str) -> TypeDetection:
    # Order matters: a refund FROM a broker is a refund, a dividend paid via a
    # broker is a dividend; only "plain" broker/MF money is a redemption.
    checks: tuple[tuple[re.Pattern[str], str, str], ...] = (
        (_SELF_TRANSFER, TXN_TRANSFER, TRANSFERS_CATEGORY),
        (_REFUND, TXN_INCOME, REFUND_CATEGORY),
        (_DIVIDEND, TXN_INCOME, DIVIDEND_CATEGORY),
        (_INTEREST, TXN_INCOME, INTEREST_CATEGORY),
        (_SALARY, TXN_INCOME, SALARY_CATEGORY),
        (_REDEMPTION, TXN_INVESTMENT_IN, INVESTMENT_REDEMPTION_CATEGORY),
        (_INVESTMENT, TXN_INVESTMENT_IN, INVESTMENT_REDEMPTION_CATEGORY),
    )
    for pattern, txn_type, category in checks:
        if found := pattern.search(text):
            return TypeDetection(txn_type, category=category, keyword=found.group(0))
    # Money came in and nothing explains it: income, but ask the user to check
    return TypeDetection(TXN_INCOME, category=OTHER_INCOME_CATEGORY, confidence=UNKNOWN_CREDIT_CONFIDENCE)


def mentions_investment_platform(description: str) -> bool:
    """True if the narration names a broker, mutual fund or savings product."""
    return bool(_INVESTMENT.search(normalize_description(description)))


def txn_type_for_category(category: str, direction: str, current_type: str | None) -> str:
    """
    The txn_type implied when the USER picks a category, so the two never
    contradict each other (callers check category_fits_direction first).
      debit:  Investments & Savings -> investment_out; Transfers keeps 'transfer'
              if it already was one (self-transfer), else it is a P2P expense.
      credit: Investment Redemption -> investment_in; Transfers -> transfer;
              any income label -> income.
    """
    if direction == CREDIT:
        if category == INVESTMENT_REDEMPTION_CATEGORY:
            return TXN_INVESTMENT_IN
        return TXN_TRANSFER if category == TRANSFERS_CATEGORY else TXN_INCOME
    if category == INVESTMENTS_CATEGORY:
        return TXN_INVESTMENT_OUT
    if category == TRANSFERS_CATEGORY and current_type == TXN_TRANSFER:
        return TXN_TRANSFER
    return TXN_EXPENSE


def detect_txn_type(description: str, direction: str, channel: str | None = None) -> TypeDetection:
    """
    Classifies a transaction's type from its raw narration and direction.
    Credits ALWAYS get a category (so they never need the LLM);
    debits get one only when they are investments or self-transfers.
    """
    text = normalize_description(description)
    if direction == CREDIT:
        return _detect_credit(text)
    return _detect_debit(text, channel)
