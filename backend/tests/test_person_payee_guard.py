# tests/test_person_payee_guard.py
#
# Privacy tests: payments to people are labelled locally and NOTHING personal
# (names, phone numbers, UPI IDs, reference numbers) reaches the LLM.
# All names and numbers are made up.

import re

import pytest

from app.agents.expense_categorizer import CategorizationResult
from app.core.config import settings
from app.models.transaction import Transaction
from app.services import categorization_service as svc
from app.services.narration_parser import parse_narration
from app.services.person_payee_guard import looks_like_person
from tests.conftest import add_tx

PERSON_NARRATIONS = [
    "UPI/412345678901/RAHUL KUMAR/rahul.k99@okhdfcbank",
    "UPI/412345678901/9876543210@ybl",                       # phone-number UPI ID
    "IMPS/P2A/412345678901/PRIYA SHARMA",
    "NEFT-HDFC0001234-ANIL MEHTA",
]
MERCHANT_NARRATIONS = [
    "UPI/412345678901/SHARMA SWEETS/sharmasweets@ybl",       # business word
    "UPI/412345678901/paytmqr281005050101@paytm",            # merchant QR code
    "UPI/412345678901/ACME TECHNOLOGIES PVT LTD/acme@icici",
    "UPI/412345678901/SWIGGY/swiggy@icici",                  # known merchant keyword
    "UPI/412345678901/GROWW/groww.brk@axisbank",             # investment platform
    "Flat payment Rahul",                                    # typed by hand: channel 'other', not guarded
    "POS 4829 RAHUL STORE",                                  # card payment
]


@pytest.mark.parametrize("narration", PERSON_NARRATIONS)
def test_person_payments_are_detected(narration):
    assert looks_like_person(narration, parse_narration(narration).channel) is True


@pytest.mark.parametrize("narration", MERCHANT_NARRATIONS)
def test_merchants_and_manual_entries_are_not_flagged(narration):
    assert looks_like_person(narration, parse_narration(narration).channel) is False


class RecordingAgent:
    """Fake LLM that records every string it is sent and answers 'Shopping'."""
    sent: list[str] = []
    examples: list = []

    def categorize_batch(self, items, examples):
        RecordingAgent.sent.extend(item.description for item in items)
        RecordingAgent.examples.extend(examples)
        return {i.item_id: CategorizationResult("Shopping", 0.8, "fake", "llm") for i in items}


@pytest.fixture(autouse=True)
def recording_llm(monkeypatch):
    RecordingAgent.sent, RecordingAgent.examples = [], []
    monkeypatch.setattr(svc, "ExpenseCategorizerAgent", RecordingAgent)
    monkeypatch.setattr(svc.time, "sleep", lambda s: None)


def _assert_nothing_personal(strings: list[str]) -> None:
    for text in strings:
        assert "@" not in text, f"UPI ID sent to LLM: {text!r}"
        assert not re.search(r"\d{4,}", text), f"number sent to LLM: {text!r}"
        assert not {"rahul", "priya", "anil", "kumar", "sharma", "mehta"} & set(text.split()), f"name sent: {text!r}"


def test_no_person_string_reaches_the_llm(db):
    for narration in PERSON_NARRATIONS:
        add_tx(db, narration)
    add_tx(db, "UPI/412345678901/ACME TECHNOLOGIES/acme.tech@icici")     # unknown MERCHANT: may go to the LLM

    summary = svc.categorize_pending_transactions(db, user_id=1)

    assert summary["person_payees_guarded"] == len(PERSON_NARRATIONS)
    assert RecordingAgent.sent == ["acme technologies acme tech"]        # cleaned, no ref number or handle
    _assert_nothing_personal(RecordingAgent.sent)
    people = db.query(Transaction).filter(Transaction.category == "Transfers").all()
    assert len(people) == len(PERSON_NARRATIONS)
    assert all(t.needs_review and t.txn_type == "expense" for t in people)


def test_person_corrections_are_not_used_as_llm_examples(db):
    person = add_tx(db, PERSON_NARRATIONS[0])
    svc.categorize_pending_transactions(db, user_id=1)
    from app.services.category_override_service import apply_user_correction
    apply_user_correction(db, 1, person.id, "Housing & Rent")         # the user says: that was rent

    add_tx(db, "UPI/412345678901/ACME TECHNOLOGIES/acme.tech@icici", day=9)
    svc.categorize_pending_transactions(db, user_id=1)
    _assert_nothing_personal([text for text, _ in RecordingAgent.examples])


def test_ablation_guard_off_shows_the_leak(db, monkeypatch):
    """With the guard switched off, person names DO reach the LLM: the privacy-leakage metric."""
    monkeypatch.setattr(settings, "CATEGORIZER_ENABLE_PERSON_GUARD", False)
    add_tx(db, PERSON_NARRATIONS[0])
    svc.categorize_pending_transactions(db, user_id=1)
    assert any("rahul" in text for text in RecordingAgent.sent)
