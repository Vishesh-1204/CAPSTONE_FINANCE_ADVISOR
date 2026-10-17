# tests/test_category_overrides.py
#
# Manual override behaviour: locks, agent_category, apply_to_similar by payee,
# remember_payee rules and their priority, unlock, bulk, direction checks.
# All narrations are synthetic.

import pytest
from fastapi import HTTPException

from app.agents.expense_categorizer import CategorizationResult
from app.models.payee_rule import PayeeRule
from app.models.transaction import Transaction
from app.services import categorization_service as svc
from app.services import category_override_service as overrides
from app.services.payee_rule_service import delete_rule
from tests.conftest import add_tx


class ShoppingAgent:
    calls = 0

    def categorize_batch(self, items, examples):
        ShoppingAgent.calls += 1
        return {i.item_id: CategorizationResult("Shopping", 0.8, "fake", "llm") for i in items}


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    ShoppingAgent.calls = 0
    monkeypatch.setattr(svc, "ExpenseCategorizerAgent", ShoppingAgent)
    monkeypatch.setattr(svc.time, "sleep", lambda s: None)


def rows(db) -> dict[int, Transaction]:
    return {t.id: t for t in db.query(Transaction).all()}


# ------------------------------------------------------------------ locks


def test_locked_rows_survive_every_agent_run_including_reset(db):
    tx = add_tx(db, "Strange shop")
    svc.categorize_pending_transactions(db, user_id=1)
    overrides.apply_user_correction(db, 1, tx.id, "Groceries")

    svc.categorize_pending_transactions(db, user_id=1, reset_ai_results=True)
    db.refresh(tx)
    assert (tx.category, tx.category_source, tx.user_locked) == ("Groceries", "user", True)
    assert tx.agent_category == "Shopping"                       # the original suggestion is kept


def test_unlock_keeps_the_category_until_a_reset_run(db):
    tx = add_tx(db, "Strange shop")
    svc.categorize_pending_transactions(db, user_id=1)
    overrides.apply_user_correction(db, 1, tx.id, "Groceries")

    unlocked = overrides.unlock_transaction(db, 1, tx.id)
    assert unlocked.user_locked is False and unlocked.category == "Groceries"

    svc.categorize_pending_transactions(db, user_id=1)           # normal run: not pending, untouched
    db.refresh(tx)
    assert tx.category == "Groceries"

    svc.categorize_pending_transactions(db, user_id=1, reset_ai_results=True)
    db.refresh(tx)
    assert tx.category == "Shopping" and tx.category_source == "llm"


def test_unlocked_rows_are_no_longer_memory(db):
    tx = add_tx(db, "Strange shop")
    overrides.apply_user_correction(db, 1, tx.id, "Groceries")
    overrides.unlock_transaction(db, 1, tx.id)

    add_tx(db, "Strange shop", day=9)
    summary = svc.categorize_pending_transactions(db, user_id=1)
    assert summary["categorized_by_memory"] == 0


# ------------------------------------------------------------------ direction + txn_type


def test_category_must_fit_the_direction(db):
    debit = add_tx(db, "Swiggy dinner")
    credit = add_tx(db, "NEFT-ACME PAYROLL", direction="credit")
    with pytest.raises(HTTPException) as error:
        overrides.apply_user_correction(db, 1, debit.id, "Salary")
    assert error.value.status_code == 422 and "debit" in error.value.detail
    with pytest.raises(HTTPException):
        overrides.apply_user_correction(db, 1, credit.id, "Food & Dining")


def test_txn_type_follows_the_users_category(db):
    groww = add_tx(db, "UPI/412345678901/GROWW/groww.brk@axisbank")
    svc.categorize_pending_transactions(db, user_id=1)
    db.refresh(groww)
    assert groww.txn_type == "investment_out"

    fixed = overrides.apply_user_correction(db, 1, groww.id, "Shopping").transaction
    assert (fixed.txn_type, fixed.investment_subtype) == ("expense", None)

    fixed = overrides.apply_user_correction(db, 1, groww.id, "Investments & Savings").transaction
    assert (fixed.txn_type, fixed.investment_subtype) == ("investment_out", "lump_sum")


# ------------------------------------------------------------------ apply_to_similar


def test_apply_to_similar_is_off_by_default(db):
    a = add_tx(db, "Strange shop", day=1)
    add_tx(db, "Strange shop", day=2)
    svc.categorize_pending_transactions(db, user_id=1)
    assert overrides.apply_user_correction(db, 1, a.id, "Groceries").similar_updated == 0


def test_apply_to_similar_matches_by_payee_and_skips_locked_and_other_direction(db):
    upi = "rajmess@okaxis"
    a = add_tx(db, f"UPI/412345678901/RAJ MESS/{upi}", day=1)
    b = add_tx(db, f"UPI/512345678901/MESS BILL SEPT/{upi}", day=2)      # same UPI ID, other text
    c = add_tx(db, f"UPI/612345678901/RAJ MESS/{upi}", day=3)
    refund = add_tx(db, f"UPI/712345678901/RAJ MESS REFUND/{upi}", day=4, direction="credit")
    other_payee = add_tx(db, "UPI/812345678901/RAJ MESS/other@okaxis", day=5)
    svc.categorize_pending_transactions(db, user_id=1)
    overrides.apply_user_correction(db, 1, c.id, "Groceries")          # c is locked now

    outcome = overrides.apply_user_correction(db, 1, a.id, "Food & Dining", apply_to_similar=True)

    current = rows(db)
    assert outcome.similar_updated == 1
    assert current[b.id].category == "Food & Dining" and current[b.id].category_source == "memory"
    assert current[c.id].category == "Groceries"                         # locked: untouched
    assert current[refund.id].category == "Refund & Cashback"            # credit: untouched
    assert current[other_payee.id].category != "Food & Dining"           # different UPI ID


# ------------------------------------------------------------------ payee rules


def test_remember_payee_keys_on_the_upi_id_and_labels_future_rows(db):
    tx = add_tx(db, "UPI/412345678901/RAJ MESS/rajmess@okaxis")
    svc.categorize_pending_transactions(db, user_id=1)
    rule = overrides.apply_user_correction(db, 1, tx.id, "Food & Dining", remember_payee=True).payee_rule
    assert (rule.match_type, rule.match_value) == ("upi_id", "rajmess@okaxis")

    later = add_tx(db, "UPI/999999999999/MONTHLY THALI/rajmess@okaxis", day=20)    # new text, same UPI ID
    summary = svc.categorize_pending_transactions(db, user_id=1)
    db.refresh(later)
    assert summary["categorized_by_payee_rules"] == 1 and ShoppingAgent.calls == 1   # only the first run
    assert (later.category, later.category_source, later.user_locked) == ("Food & Dining", "payee_rule", False)


def test_remember_payee_without_upi_uses_the_merchant_key(db):
    tx = add_tx(db, "POS 4829 RAJ MESS")
    rule = overrides.apply_user_correction(db, 1, tx.id, "Food & Dining", remember_payee=True).payee_rule
    assert (rule.match_type, rule.match_value) == ("merchant_key", "raj mess")


def test_payee_rule_beats_memory_and_keyword_rules(db):
    tx = add_tx(db, "UPI/412345678901/SWIGGY/swiggy@icici")
    overrides.apply_user_correction(db, 1, tx.id, "Groceries", remember_payee=True)   # user's own choice

    later = add_tx(db, "UPI/512345678901/SWIGGY/swiggy@icici", day=9)
    svc.categorize_pending_transactions(db, user_id=1)
    db.refresh(later)
    assert later.category == "Groceries" and later.category_source == "payee_rule"   # not 'Food & Dining'


def test_payee_rule_never_labels_a_credit_from_the_same_payee(db):
    tx = add_tx(db, "UPI/412345678901/SWIGGY/swiggy@icici")
    overrides.apply_user_correction(db, 1, tx.id, "Food & Dining", remember_payee=True)
    refund = add_tx(db, "UPI/512345678901/SWIGGY REFUND/swiggy@icici", day=9, direction="credit")
    svc.categorize_pending_transactions(db, user_id=1)
    db.refresh(refund)
    assert refund.category == "Refund & Cashback"


def test_remembering_again_updates_the_single_rule(db):
    tx = add_tx(db, "UPI/412345678901/RAJ MESS/rajmess@okaxis")
    overrides.apply_user_correction(db, 1, tx.id, "Food & Dining", remember_payee=True)
    overrides.apply_user_correction(db, 1, tx.id, "Groceries", remember_payee=True)
    rules = db.query(PayeeRule).all()
    assert len(rules) == 1 and rules[0].category == "Groceries"


def test_rules_are_per_user(db):
    mine = add_tx(db, "UPI/412345678901/RAJ MESS/rajmess@okaxis", user_id=1)
    rule = overrides.apply_user_correction(db, 1, mine.id, "Food & Dining", remember_payee=True).payee_rule
    theirs = add_tx(db, "UPI/412345678901/RAJ MESS/rajmess@okaxis", user_id=2)
    svc.categorize_pending_transactions(db, user_id=2)
    db.refresh(theirs)
    assert theirs.category_source != "payee_rule"
    with pytest.raises(HTTPException) as error:
        delete_rule(db, user_id=2, rule_id=rule.id)
    assert error.value.status_code == 404


# ------------------------------------------------------------------ bulk


def test_bulk_updates_own_fitting_rows_and_reports_the_rest(db):
    a = add_tx(db, "Strange shop")
    b = add_tx(db, "Odd store")
    credit = add_tx(db, "NEFT-XYZ", direction="credit")
    someone_elses = add_tx(db, "Strange shop", user_id=2)

    result = overrides.bulk_set_category(db, 1, [a.id, b.id, credit.id, someone_elses.id, 9999, a.id], "Groceries")

    assert result["updated"] == 2 and result["updated_ids"] == [a.id, b.id]
    assert result["skipped_incompatible_ids"] == [credit.id]
    assert result["not_found_ids"] == [someone_elses.id, 9999]
    current = rows(db)
    assert all(current[i].user_locked and current[i].category == "Groceries" for i in (a.id, b.id))
    assert current[someone_elses.id].category is None
