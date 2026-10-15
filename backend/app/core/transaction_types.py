# app/core/transaction_types.py
#
# Purpose: the allowed values for the transaction "shape" columns
# (direction, txn_type, investment_subtype, channel). Kept as constants in one
# place so services, schemas and tests never use a misspelled string.

# Which way the money moved, seen from the user's bank account
DEBIT = "debit"      # money out
CREDIT = "credit"    # money in
DIRECTIONS: tuple[str, ...] = (DEBIT, CREDIT)

# What KIND of money movement it is. Only EXPENSE counts toward spending totals.
TXN_EXPENSE = "expense"
TXN_INCOME = "income"
TXN_INVESTMENT_OUT = "investment_out"   # SIP, broker top-up, FD booking
TXN_INVESTMENT_IN = "investment_in"     # redemption / withdrawal from broker or MF (NOT income)
TXN_TRANSFER = "transfer"               # between the user's own accounts
TXN_TYPES: tuple[str, ...] = (TXN_EXPENSE, TXN_INCOME, TXN_INVESTMENT_OUT, TXN_INVESTMENT_IN, TXN_TRANSFER)

# Sub-kinds of investment_out
SUBTYPE_SIP = "sip"                     # recurring mandate (NACH / SIP)
SUBTYPE_LUMP_SUM = "lump_sum"           # one-time purchase, FD, PPF
SUBTYPE_WALLET_TOPUP = "wallet_topup"   # money added to a broker's trading balance
INVESTMENT_SUBTYPES: tuple[str, ...] = (SUBTYPE_SIP, SUBTYPE_LUMP_SUM, SUBTYPE_WALLET_TOPUP)

# Payment rail, read from the narration ("UPI/...", "NEFT-...", "POS 4829 ...")
CHANNEL_UPI = "upi"
CHANNEL_NEFT = "neft"
CHANNEL_IMPS = "imps"
CHANNEL_NACH = "nach"
CHANNEL_CARD = "card"
CHANNEL_ATM = "atm"
CHANNEL_CHEQUE = "cheque"
CHANNEL_OTHER = "other"
CHANNELS: tuple[str, ...] = (
    CHANNEL_UPI, CHANNEL_NEFT, CHANNEL_IMPS, CHANNEL_NACH, CHANNEL_CARD, CHANNEL_ATM, CHANNEL_CHEQUE, CHANNEL_OTHER,
)
