# app/core/merchant_dictionary.py
#
# Purpose: keyword DATA for recognising investment platforms and income
# sources in bank narrations. Only data lives here; the logic that uses it is
# app/services/txn_type_detector.py (and rule_based_categorizer.py reuses the
# investment words), so updating a broker name is a one-line change.
#
# All keywords are matched against the NORMALISED description
# (lowercase, punctuation/digits/UPI handles removed) on word boundaries.

# Stock brokers / investment apps. Money sent to them is usually a top-up of
# the trading balance; money received from them is a withdrawal/redemption.
BROKER_KEYWORDS: tuple[str, ...] = (
    "groww", "zerodha", "angel one", "angelone", "angel broking", "upstox", "indmoney",
    "paytm money", "5paisa", "icicidirect", "icici direct", "hdfc securities",
    "kotak securities", "motilal oswal", "kuvera",
)

# Mutual-fund plumbing: SIP mandates, AMCs, registrars and exchange platforms.
# ('amc' is deliberately missing: it also means "annual maintenance contract".)
MUTUAL_FUND_KEYWORDS: tuple[str, ...] = (
    "mutual fund", "mf", "sip", "bse star mf", "bsestarmf", "iccl", "indian clearing",
    "nse clearing", "cams", "kfintech", "kfin", "nippon india", "icici pru", "mirae asset",
    "parag parikh", "ppfas", "hdfc mf", "sbi mf", "axis mf", "uti mf", "kotak mf", "dsp mf",
)

# Other savings products (money parked, not spent)
SAVINGS_PRODUCT_KEYWORDS: tuple[str, ...] = (
    "ppf", "nps", "fixed deposit", "recurring deposit", "fd booking", "sukanya samriddhi", "demat",
)

# Everything that marks a DEBIT as an investment outflow
INVESTMENT_KEYWORDS: tuple[str, ...] = BROKER_KEYWORDS + MUTUAL_FUND_KEYWORDS + SAVINGS_PRODUCT_KEYWORDS

# Words that mark a SIP / mandate-style recurring investment
SIP_KEYWORDS: tuple[str, ...] = ("sip", "mandate", "autopay")

# Words that mean "money came back from an investment" on a CREDIT
REDEMPTION_KEYWORDS: tuple[str, ...] = (
    "redemption", "redeem", "redeemed", "sip redeem", "maturity", "fd closure", "withdrawal",
)

# Moving money between the user's own accounts (neither spent nor earned)
SELF_TRANSFER_KEYWORDS: tuple[str, ...] = (
    "self", "self transfer", "to self", "from self", "own account", "own acc", "own a c",
)

# Credit-side income sources
SALARY_KEYWORDS: tuple[str, ...] = ("salary", "sal", "payroll", "wages", "stipend")
INTEREST_KEYWORDS: tuple[str, ...] = (
    "interest", "int pd", "int paid", "int credit", "int cr", "sb int", "fd int", "rd int", "savings int",
)
DIVIDEND_KEYWORDS: tuple[str, ...] = ("dividend", "div")
REFUND_KEYWORDS: tuple[str, ...] = ("refund", "cashback", "cash back", "reversal", "reversed", "rev", "chargeback")
