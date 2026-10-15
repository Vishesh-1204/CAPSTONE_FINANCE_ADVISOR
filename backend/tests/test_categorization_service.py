# tests/test_categorization_service.py
#
# Tests the orchestrator: memory -> rules -> LLM, using a fake agent
# so no network is needed.

import pytest
from fastapi import HTTPException

from app.agents.expense_categorizer import CategorizationResult
from app.core.config import settings
from app.models.transaction import Transaction
from app.services import categorization_service as svc
from app.services import category_override_service as overrides
from app.services.text_normalizer import normalize_description
from tests.conftest import add_tx


class FakeAgent:
    """Records what it was asked and answers from a {description: (category, confidence)} table."""
    instances: list["FakeAgent"] = []

    def __init__(self, answers=None):
        self.answers = FakeAgent.answers if answers is None else answers
        self.batches: list[list] = []
        self.examples_seen: list = []
        FakeAgent.instances.append(self)

    answers: dict = {}

    def categorize_batch(self, items, examples):
        # The service sends CLEANED descriptions, so answers are looked up by normalized key
        self.batches.append(items)
        self.examples_seen.append(examples)
        answers = {normalize_description(text): value for text, value in self.answers.items()}
        out = {}
        for item in items:
            if item.description in answers:
                category, confidence = answers[item.description]
                out[item.item_id] = CategorizationResult(category, confidence, "fake reason", "llm")
        return out


@pytest.fixture(autouse=True)
def fast_fake_llm(monkeypatch):
    """Use the fake agent, and don't actually sleep between batches."""
    FakeAgent.instances = []
    FakeAgent.answers = {}
    monkeypatch.setattr(svc, "ExpenseCategorizerAgent", FakeAgent)
    monkeypatch.setattr(svc.time, "sleep", lambda s: None)


def test_rules_handle_obvious_rows_without_calling_the_llm(db):
    add_tx(db, "Swiggy order - dinner")
    add_tx(db, "Netflix monthly subscription")
    summary = svc.categorize_pending_transactions(db, user_id=1)

    assert summary["categorized_by_rules"] == 2 and summary["categorized_by_llm"] == 0
    assert FakeAgent.instances == []                    # LLM never even constructed
    rows = {t.description: t for t in db.query(Transaction).all()}
    assert rows["Swiggy order - dinner"].category == "Food & Dining"
    assert rows["Swiggy order - dinner"].category_source == "rule"


def test_identical_descriptions_are_sent_to_the_llm_once_with_occurrences(db):
    for day in (1, 2, 3):
        add_tx(db, "Flat payment Rahul", 25000.0, day=day)
    FakeAgent.answers = {"Flat payment Rahul": ("Housing & Rent", 0.9)}

    summary = svc.categorize_pending_transactions(db, user_id=1)

    assert summary["categorized_by_llm"] == 3 and summary["failed"] == 0
    sent = FakeAgent.instances[0].batches[0]
    assert len(sent) == 1 and sent[0].occurrences == 3 and sent[0].amount == 25000.0
    assert {t.category for t in db.query(Transaction).all()} == {"Housing & Rent"}


def test_failed_llm_rows_stay_uncategorized_so_they_are_retried(db):
    add_tx(db, "Mystery merchant xyz")
    FakeAgent.answers = {}                              # the fake "fails" for everything
    summary = svc.categorize_pending_transactions(db, user_id=1)

    assert summary["failed"] == 1
    assert db.query(Transaction).one().category is None      # NOT silently labelled 'Other'
    assert "could not be categorized" in summary["message"]   # the user is told why

    FakeAgent.answers = {"Mystery merchant xyz": ("Shopping", 0.8)}
    summary = svc.categorize_pending_transactions(db, user_id=1)   # second run picks it up
    assert summary["categorized_by_llm"] == 1 and db.query(Transaction).one().category == "Shopping"


def test_low_confidence_is_flagged_for_review(db):
    add_tx(db, "Strange payment")
    FakeAgent.answers = {"Strange payment": ("Other", 0.3)}
    summary = svc.categorize_pending_transactions(db, user_id=1)

    assert summary["needs_review"] == 1
    assert db.query(Transaction).one().needs_review is True


def test_batches_respect_batch_size_and_stop_after_repeated_failures(db, monkeypatch):
    monkeypatch.setattr(settings, "CATEGORIZER_BATCH_SIZE", 2)
    # distinct words on purpose: digits are stripped by the normalizer, so "shop 1"/"shop 2" would merge
    for name in ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet"]:
        add_tx(db, f"Unknown {name} store")
    FakeAgent.answers = {}                              # every batch fails

    summary = svc.categorize_pending_transactions(db, user_id=1)

    assert len(FakeAgent.instances[0].batches) == 2     # gave up after 2 failed batches in a row
    assert summary["failed"] == 10 and "Stopped early" in summary["message"]


def test_missing_llm_configuration_still_saves_rule_matches(db, monkeypatch):
    def broken_agent():
        raise ValueError("GEMINI_API_KEY is not set")
    monkeypatch.setattr(svc, "ExpenseCategorizerAgent", broken_agent)
    add_tx(db, "Swiggy dinner")
    add_tx(db, "Unknown thing")

    summary = svc.categorize_pending_transactions(db, user_id=1)

    assert summary["categorized_by_rules"] == 1 and summary["failed"] == 1
    assert "GEMINI_API_KEY" in summary["message"]


def test_user_correction_becomes_memory_for_future_rows(db):
    first = add_tx(db, "Raj Mess monthly")
    FakeAgent.answers = {"Raj Mess monthly": ("Other", 0.4)}
    svc.categorize_pending_transactions(db, user_id=1)

    overrides.apply_user_correction(db, 1, first.id, "Food & Dining", apply_to_similar=False)

    add_tx(db, "Raj Mess monthly", day=20)                      # same description arrives later
    FakeAgent.instances = []
    summary = svc.categorize_pending_transactions(db, user_id=1)

    assert summary["categorized_by_memory"] == 1
    assert FakeAgent.instances == []                            # no LLM needed
    newest = db.query(Transaction).order_by(Transaction.id.desc()).first()
    assert newest.category == "Food & Dining" and newest.category_source == "memory"


def test_correction_fixes_similar_rows_and_feeds_llm_examples(db):
    a = add_tx(db, "Raj Mess monthly", day=1)
    add_tx(db, "Raj Mess monthly", day=2)
    FakeAgent.answers = {"Raj Mess monthly": ("Other", 0.4)}
    svc.categorize_pending_transactions(db, user_id=1)

    similar = overrides.apply_user_correction(db, 1, a.id, "Food & Dining", apply_to_similar=True).similar_updated
    assert similar == 1
    assert {t.category for t in db.query(Transaction).all()} == {"Food & Dining"}

    add_tx(db, "Totally new description")
    FakeAgent.answers = {"Totally new description": ("Other", 0.5)}
    FakeAgent.instances = []
    svc.categorize_pending_transactions(db, user_id=1)
    assert ("raj mess monthly", "Food & Dining") in FakeAgent.instances[0].examples_seen[0]   # cleaned text only


def test_user_confirmed_categories_survive_a_reset(db):
    tx = add_tx(db, "Raj Mess monthly")
    add_tx(db, "Swiggy dinner")
    svc.categorize_pending_transactions(db, user_id=1)
    overrides.apply_user_correction(db, 1, tx.id, "Groceries", apply_to_similar=False)

    svc.categorize_pending_transactions(db, user_id=1, reset_ai_results=True)

    rows = {t.description: t for t in db.query(Transaction).all()}
    assert rows["Raj Mess monthly"].category == "Groceries" and rows["Raj Mess monthly"].category_source == "user"
    assert rows["Swiggy dinner"].category == "Food & Dining"    # redone, same answer


def test_users_cannot_correct_each_others_transactions(db):
    other_users_tx = add_tx(db, "Swiggy dinner", user_id=2)
    with pytest.raises(HTTPException) as error:
        overrides.apply_user_correction(db, user_id=1, transaction_id=other_users_tx.id, category="Shopping", apply_to_similar=True)
    assert error.value.status_code == 404


def test_memory_is_per_user(db):
    mine = add_tx(db, "Raj Mess monthly", user_id=1)
    svc.categorize_pending_transactions(db, user_id=1)
    overrides.apply_user_correction(db, 1, mine.id, "Food & Dining", apply_to_similar=False)

    add_tx(db, "Raj Mess monthly", user_id=2)                   # a different user, same text
    FakeAgent.answers = {"Raj Mess monthly": ("Groceries", 0.8)}
    summary = svc.categorize_pending_transactions(db, user_id=2)

    assert summary["categorized_by_memory"] == 0 and summary["categorized_by_llm"] == 1


def test_one_users_run_never_touches_another_users_rows(db):
    add_tx(db, "Swiggy dinner", user_id=1)
    add_tx(db, "Swiggy dinner", user_id=2)
    svc.categorize_pending_transactions(db, user_id=1)
    other = db.query(Transaction).filter(Transaction.user_id == 2).one()
    assert other.category is None


# ---------------------------------------------------------------- v3 (S2): types


def test_old_rows_get_txn_type_backfilled_on_the_next_run(db):
    tx = add_tx(db, "Swiggy dinner")
    assert tx.txn_type is None                                   # like a row saved before v3
    svc.categorize_pending_transactions(db, user_id=1)
    db.refresh(tx)
    assert tx.txn_type == "expense" and tx.channel == "other" and tx.payee_key == "swiggy dinner"


def test_investment_debit_is_labelled_without_the_llm(db):
    add_tx(db, "NACH/HDFC MF SIP/0012345", 5000.0)
    summary = svc.categorize_pending_transactions(db, user_id=1)

    tx = db.query(Transaction).one()
    assert summary["categorized_by_rules"] == 1 and FakeAgent.instances == []
    assert (tx.txn_type, tx.investment_subtype, tx.category) == ("investment_out", "sip", "Investments & Savings")


def test_credits_never_reach_the_llm(db):
    add_tx(db, "SALARY CREDIT SEP", 90000.0, direction="credit")
    add_tx(db, "NEFT-XYZ ENTERPRISES", 1200.0, direction="credit")     # unknown source
    summary = svc.categorize_pending_transactions(db, user_id=1)

    assert FakeAgent.instances == [] and summary["failed"] == 0
    rows = {t.description: t for t in db.query(Transaction).all()}
    assert rows["SALARY CREDIT SEP"].category == "Salary"
    assert rows["NEFT-XYZ ENTERPRISES"].category == "Other Income"
    assert rows["NEFT-XYZ ENTERPRISES"].needs_review is True and summary["needs_review"] == 1


def test_same_text_as_debit_and_credit_is_decided_separately(db):
    add_tx(db, "Groww", 2000.0, direction="debit")
    add_tx(db, "Groww", 2500.0, direction="credit")
    svc.categorize_pending_transactions(db, user_id=1)

    by_direction = {t.direction: t for t in db.query(Transaction).all()}
    assert by_direction["debit"].category == "Investments & Savings"
    assert by_direction["credit"].category == "Investment Redemption"
    assert by_direction["credit"].txn_type == "investment_in"


def test_memory_does_not_leak_from_debit_to_credit(db):
    debit = add_tx(db, "Raj Mess monthly", direction="debit")
    FakeAgent.answers = {"Raj Mess monthly": ("Other", 0.4)}
    svc.categorize_pending_transactions(db, user_id=1)
    overrides.apply_user_correction(db, 1, debit.id, "Food & Dining", apply_to_similar=False)

    add_tx(db, "Raj Mess monthly", direction="credit", day=9)        # e.g. a refund from the mess
    summary = svc.categorize_pending_transactions(db, user_id=1)
    assert summary["categorized_by_memory"] == 0
    credit = db.query(Transaction).filter(Transaction.direction == "credit").one()
    assert credit.category == "Other Income"


def test_agent_category_keeps_the_original_suggestion_after_a_correction(db):
    tx = add_tx(db, "Strange shop")
    FakeAgent.answers = {"Strange shop": ("Shopping", 0.7)}
    svc.categorize_pending_transactions(db, user_id=1)
    corrected = overrides.apply_user_correction(db, 1, tx.id, "Groceries", apply_to_similar=False).transaction

    assert corrected.category == "Groceries" and corrected.agent_category == "Shopping"
    assert corrected.user_locked is True


def test_decimal_amounts_reach_the_llm_as_plain_floats(db):
    add_tx(db, "Mystery merchant", 99.10)
    FakeAgent.answers = {"Mystery merchant": ("Shopping", 0.8)}
    svc.categorize_pending_transactions(db, user_id=1)
    sent = FakeAgent.instances[0].batches[0][0]
    assert isinstance(sent.amount, float) and sent.amount == 99.1
