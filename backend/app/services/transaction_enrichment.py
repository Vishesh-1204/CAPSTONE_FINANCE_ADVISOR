# app/services/transaction_enrichment.py
#
# Purpose: the ONE function every ingestion path calls before saving a row
# (manual entry, CSV today; XLSX/PDF in S4-S5) and the categorizer calls for
# old rows. It fills the derived columns from the raw narration:
#   description_key, payee_key, channel, txn_type, investment_subtype
# so no import format duplicates this logic.

from app.core.transaction_types import DEBIT
from app.models.transaction import Transaction
from app.services.narration_parser import parse_narration
from app.services.txn_type_detector import detect_txn_type


def enrich_transaction(transaction: Transaction) -> None:
    """Sets the derived columns on a (new or old) transaction in place. No DB access."""
    if not transaction.direction:
        transaction.direction = DEBIT          # Python-side default only applies at INSERT time
    info = parse_narration(transaction.description)
    detection = detect_txn_type(transaction.description, transaction.direction, info.channel)

    transaction.description_key = info.description_key
    transaction.payee_key = info.payee_key
    transaction.channel = info.channel
    transaction.txn_type = detection.txn_type
    transaction.investment_subtype = detection.investment_subtype
