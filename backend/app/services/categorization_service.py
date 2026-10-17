# app/services/categorization_service.py
#
# Purpose: runs the Expense Intelligence Agent over a user's transactions.
# This is the ORCHESTRATOR and the single place that defines the priority
# order (spec section 4). For each uncategorized, UNLOCKED transaction it tries
# the cheapest, most reliable method first and only asks the LLM when it must:
#
#   0  LOCK         - rows the user set (user_locked) are never touched
#   1  PAYEE RULES  - the user's "remember this payee" rules (UPI ID, then merchant key)
#   2  MEMORY       - "you told me this exact description is Groceries before"
#   3  RULES        - a) investment/income/transfer dictionary (txn_type detector)
#                     b) expense keyword match (Swiggy, Netflix ...), exactly one category
#   4  PERSON GUARD - looks like a payment to a person -> Transfers + needs_review,
#                     and it is NEVER sent to the LLM (privacy)
#   5  LLM          - batched; receives only CLEANED merchant strings (no UPI IDs,
#                     reference numbers or digits) and the primary->fallback chain
#
# Only debits of type 'expense' ever reach the LLM: credits always get a
# deterministic label. Debits and credits are grouped separately: a "Groww"
# debit (investment) and a "Groww" credit (redemption) never share a decision.
# A failed LLM call leaves the row UNCATEGORIZED (retried next run), never "Other".

import logging
import statistics
import time
from collections import defaultdict
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.agents.expense_categorizer import CategorizationResult, ExpenseCategorizerAgent, PendingItem
from app.core.categories import TRANSFERS_CATEGORY
from app.core.config import settings
from app.core.transaction_types import CREDIT
from app.models.transaction import Transaction
from app.services.payee_rule_service import PayeeRuleIndex, load_rule_index
from app.services.person_payee_guard import looks_like_person
from app.services.rule_based_categorizer import RULE_CONFIDENCE, rule_categorize
from app.services.text_normalizer import normalize_description
from app.services.transaction_enrichment import enrich_transaction, sync_txn_type_with_category
from app.services.txn_type_detector import detect_txn_type

logger = logging.getLogger(__name__)

# Maps a result's source to the summary counter it increments
_SUMMARY_FIELD = {
    "payee_rule": "categorized_by_payee_rules",
    "memory": "categorized_by_memory",
    "rule": "categorized_by_rules",
    "llm": "categorized_by_llm",
}

# Stop calling the LLM after this many batches in a row fail (quota used up, bad key...)
_MAX_CONSECUTIVE_FAILURES = 2

# A person-payee guess is deliberately below the review threshold
PERSON_GUARD_CONFIDENCE = 0.5
PERSON_GUARD_REASON = "Looks like a payment to a person; please confirm."

# Rows are grouped by (direction, description_key)
GroupKey = tuple[str, str]


def _apply(transaction: Transaction, result: CategorizationResult) -> None:
    """Writes an AGENT decision onto a transaction row (never used for locked rows)."""
    if transaction.agent_category is None:
        # Remember the agent's FIRST suggestion, so overrides can be measured later
        transaction.agent_category = result.category
    transaction.category = result.category
    transaction.category_confidence = result.confidence
    transaction.category_reasoning = result.reasoning
    transaction.category_source = result.source
    transaction.needs_review = result.confidence < settings.CATEGORIZER_REVIEW_THRESHOLD
    sync_txn_type_with_category(transaction)   # e.g. LLM says "Investments & Savings" -> investment_out


def _load_memory(db: Session, user_id: int, keys: set[GroupKey]) -> dict[GroupKey, str]:
    """
    (direction, description_key) -> category for rows the USER locked.
    Ordered oldest to newest, so a newer correction overwrites an older one.
    """
    if not keys:
        return {}
    rows = (
        db.query(Transaction.direction, Transaction.description_key, Transaction.category)
        .filter(
            Transaction.user_id == user_id,
            Transaction.user_locked.is_(True),
            Transaction.description_key.in_({key for _, key in keys}),
        )
        .order_by(Transaction.id.asc())
        .all()
    )
    return {(direction, key): category for direction, key, category in rows if (direction, key) in keys}


def _load_examples(db: Session, user_id: int) -> list[tuple[str, str]]:
    """
    The user's most recent distinct corrections, shown to the LLM as examples.
    PRIVACY: only the cleaned description_key is used, and corrections of
    person-like payees are skipped entirely.
    """
    limit = settings.CATEGORIZER_FEW_SHOT_LIMIT
    rows = (
        db.query(Transaction)
        .filter(Transaction.user_id == user_id, Transaction.user_locked.is_(True), Transaction.direction != CREDIT)
        .order_by(Transaction.id.desc())
        .limit(limit * 3)           # fetch extra: duplicates and person payees get dropped below
        .all()
    )
    seen: set[str] = set()
    examples: list[tuple[str, str]] = []
    for row in rows:
        key = normalize_description(row.description)
        if key in seen or looks_like_person(row.description, row.channel):
            continue
        seen.add(key)
        examples.append((key, row.category))
        if len(examples) >= limit:
            break
    return examples


def _reset_ai_results(db: Session, user_id: int) -> int:
    """Clears every category on UNLOCKED rows, so the agent redoes them. Locked rows are kept."""
    cleared = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.category.is_not(None),
            Transaction.user_locked.is_(False),
        )
        .update(
            {
                Transaction.category: None,
                Transaction.category_confidence: None,
                Transaction.category_reasoning: None,
                Transaction.category_source: None,
                Transaction.agent_category: None,      # the row is redone from scratch
                Transaction.needs_review: False,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    logger.info(f"Reset {cleared} unlocked categories for user_id={user_id}")
    return cleared


def _backfill_types(db: Session, user_id: int) -> int:
    """
    Rows saved before v3 (or by an old import) have no txn_type yet. Derive it,
    plus channel and payee_key, from the narration. Local and cheap: no LLM.
    """
    rows = db.query(Transaction).filter(Transaction.user_id == user_id, Transaction.txn_type.is_(None)).all()
    for transaction in rows:
        enrich_transaction(transaction)
    if rows:
        db.commit()
        logger.info(f"Backfilled txn_type for {len(rows)} transactions of user_id={user_id}")
    return len(rows)


def _deterministic_result(rows: list[Transaction]) -> CategorizationResult | None:
    """
    Layer 3. a) The type detector: investments, self-transfers and EVERY credit
    get a label here. b) Expense keyword rules for ordinary debits.
    Returns None when only the guard or the LLM can decide.
    """
    sample = rows[0]
    detection = detect_txn_type(sample.description, sample.direction, sample.channel)
    if detection.category is not None:
        reason = (
            f"Matched '{detection.keyword}' ({detection.txn_type})." if detection.keyword
            else "Money received with no recognisable source; please check."
        )
        return CategorizationResult(detection.category, detection.confidence, reason, "rule")

    if settings.CATEGORIZER_ENABLE_RULES:
        match = rule_categorize(sample.description)
        if match:
            return CategorizationResult(match.category, RULE_CONFIDENCE, f"Matched keyword '{match.keyword}'.", "rule")
    return None


def _apply_payee_rules(
    pending: list[Transaction], rules: PayeeRuleIndex, summary: dict
) -> list[Transaction]:
    """
    Layer 1, per ROW (not per group): two rows can share a description_key but
    have different UPI IDs. Returns the rows no rule matched.
    """
    remaining: list[Transaction] = []
    for transaction in pending:
        matched = rules.match(transaction)
        if matched is None:
            remaining.append(transaction)
            continue
        category, match_type = matched
        _apply(transaction, CategorizationResult(category, 1.0, f"Your payee rule ({match_type}).", "payee_rule"))
        summary["categorized_by_payee_rules"] += 1
    return remaining


# Builds the LLM agent. Injected by the evaluation harness (to record what is sent);
# None means the real ExpenseCategorizerAgent.
AgentFactory = Callable[[], ExpenseCategorizerAgent]


def _categorize_with_llm(
    db: Session,
    user_id: int,
    unresolved: dict[GroupKey, list[Transaction]],
    summary: dict,
    agent_factory: AgentFactory | None = None,
) -> None:
    """Layer 5: sends the leftover unique CLEANED descriptions to the LLM, batch by batch."""
    total_rows = sum(len(rows) for rows in unresolved.values())

    try:
        agent = (agent_factory or ExpenseCategorizerAgent)()
    except Exception as exc:  # most often: API key missing for every configured provider
        logger.error(f"LLM unavailable: {exc}")
        summary["failed"] += total_rows
        summary["message"] = (
            f"LLM unavailable ({exc}). Rule and memory matches were saved; "
            "fix the configuration and run /categorize again for the rest."
        )
        return

    examples = _load_examples(db, user_id)
    keys = list(unresolved)
    batch_size = settings.CATEGORIZER_BATCH_SIZE
    consecutive_failures = 0

    for start in range(0, len(keys), batch_size):
        batch_keys = keys[start:start + batch_size]

        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            skipped = sum(len(unresolved[k]) for k in keys[start:])
            summary["failed"] += skipped
            summary["message"] = "Stopped early: the LLM kept failing (quota or key problem). Run again later."
            logger.error(f"Aborting LLM stage, {skipped} transactions left uncategorized")
            return

        if start > 0:
            time.sleep(settings.CATEGORIZER_BATCH_DELAY_SECONDS)   # respect free-tier rate limits

        # One item per UNIQUE description. PRIVACY: the description sent is the
        # group's cleaned key (lowercase, no digits / UPI handles / rail words),
        # never the raw narration.
        items = [
            PendingItem(
                item_id=index,
                description=key[1],
                # float(): amounts are Decimal in the DB, and JSON cannot encode Decimal
                amount=float(statistics.median(t.amount for t in unresolved[key])),
                occurrences=len(unresolved[key]),
            )
            for index, key in enumerate(batch_keys)
        ]
        results = agent.categorize_batch(items, examples)
        consecutive_failures = consecutive_failures + 1 if not results else 0

        for index, key in enumerate(batch_keys):
            rows = unresolved[key]
            result = results.get(index)
            if result is None:
                summary["failed"] += len(rows)     # leave category empty so it is retried next run
                continue
            for transaction in rows:
                _apply(transaction, result)
            summary["categorized_by_llm"] += len(rows)

        db.commit()   # save progress after every batch, so a crash never loses finished work


def categorize_pending_transactions(
    db: Session, user_id: int, reset_ai_results: bool = False, agent_factory: AgentFactory | None = None
) -> dict:
    """
    Categorizes every uncategorized, unlocked transaction of one user.

    reset_ai_results=True first clears all UNLOCKED categories, which is how you
    re-run the improved agent over old data. Locked (user-set) rows are kept.
    agent_factory: optional LLM agent builder (the evaluation injects a recorder).
    """
    if reset_ai_results:
        _reset_ai_results(db, user_id)
    _backfill_types(db, user_id)

    pending = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.category.is_(None),
            Transaction.user_locked.is_(False),
        )
        .order_by(Transaction.id.asc())
        .all()
    )
    summary: dict = {
        "total_processed": len(pending), "categorized_by_payee_rules": 0, "categorized_by_memory": 0,
        "categorized_by_rules": 0, "categorized_by_llm": 0, "person_payees_guarded": 0,
        "needs_review": 0, "failed": 0, "message": None,
    }
    if not pending:
        return summary

    for transaction in pending:
        transaction.description_key = normalize_description(transaction.description)

    remaining = pending
    if settings.CATEGORIZER_ENABLE_PAYEE_RULES:
        remaining = _apply_payee_rules(pending, load_rule_index(db, user_id), summary)

    # Group identical descriptions: classify "Monthly house rent" once, apply to all 3 rows.
    # Direction is part of the key: the same text as debit and as credit means different things.
    groups: dict[GroupKey, list[Transaction]] = defaultdict(list)
    for transaction in remaining:
        groups[(transaction.direction, transaction.description_key)].append(transaction)

    memory = _load_memory(db, user_id, set(groups)) if settings.CATEGORIZER_ENABLE_MEMORY else {}

    unresolved: dict[GroupKey, list[Transaction]] = {}
    for key, rows in groups.items():
        result: CategorizationResult | None
        if key in memory:
            result = CategorizationResult(memory[key], 1.0, "Matches a category you confirmed earlier.", "memory")
        else:
            result = _deterministic_result(rows)

        if result is None and settings.CATEGORIZER_ENABLE_PERSON_GUARD:
            if looks_like_person(rows[0].description, rows[0].channel):
                guarded = CategorizationResult(
                    TRANSFERS_CATEGORY, PERSON_GUARD_CONFIDENCE, PERSON_GUARD_REASON, "rule",
                )
                for transaction in rows:
                    _apply(transaction, guarded)
                    transaction.needs_review = True        # always reviewed, whatever the threshold
                summary["person_payees_guarded"] += len(rows)
                continue

        if result is None:
            if key[0] == CREDIT:     # defensive: the detector always labels credits
                logger.error("A credit reached the LLM stage; skipping it (detector bug)")
                summary["failed"] += len(rows)
                continue
            unresolved[key] = rows
            continue
        for transaction in rows:
            _apply(transaction, result)
        summary[_SUMMARY_FIELD[result.source]] += len(rows)
    db.commit()

    if summary["person_payees_guarded"]:
        # Count only: the narrations themselves are personal data
        logger.info(f"Person-payee guard kept {summary['person_payees_guarded']} rows away from the LLM")

    if unresolved:
        _categorize_with_llm(db, user_id, unresolved, summary, agent_factory)

    summary["needs_review"] = sum(1 for t in pending if t.needs_review)
    if summary["failed"] and not summary["message"]:
        summary["message"] = (
            f"{summary['failed']} transaction(s) could not be categorized (LLM error or rate limit). "
            "They were left empty, so run /categorize again later. Check the server logs for details."
        )
    db.commit()

    logger.info(f"Categorization run for user_id={user_id}: { {k: v for k, v in summary.items() if k != 'message'} }")
    return summary
