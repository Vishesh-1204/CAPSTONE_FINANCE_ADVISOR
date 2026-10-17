# tests/test_synthetic_history.py
#
# The synthetic budget history must be reproducible (same seed -> same rows),
# use only real labels, cover 3 personas x 6 months, and contain the money
# movements the planner has to IGNORE (investments, self-transfers, refunds...).

import re
from collections import Counter

from app.core.categories import ALL_CATEGORY_NAMES, REFUND_CATEGORY
from app.core.transaction_types import CREDIT, DEBIT, TXN_TYPES
from scripts.make_synthetic_history import MONTHS, PERSONAS, generate_history, read_csv, write_csv


def test_same_seed_gives_identical_rows_and_a_different_seed_does_not():
    assert generate_history(42) == generate_history(42)
    assert generate_history(42) != generate_history(7)


def test_csv_round_trip_is_lossless(tmp_path):
    rows = generate_history()
    write_csv(rows, tmp_path / "history.csv")
    assert read_csv(tmp_path / "history.csv") == rows


def test_every_label_is_a_real_category_and_type():
    rows = generate_history()
    assert {r.category for r in rows} <= set(ALL_CATEGORY_NAMES)
    assert {r.txn_type for r in rows} <= set(TXN_TYPES)
    assert {r.direction for r in rows} <= {DEBIT, CREDIT}
    assert all(r.amount > 0 for r in rows)


def test_three_personas_times_six_months():
    rows = generate_history()
    covered = Counter((r.persona, f"{r.day:%Y-%m}") for r in rows)
    assert {p.name for p in PERSONAS} == {r.persona for r in rows}
    assert set(covered) == {(p.name, m) for p in PERSONAS for m in MONTHS}


def test_each_persona_has_rows_that_are_not_spending():
    rows = generate_history()
    for persona in PERSONAS:
        kinds = {(r.txn_type, r.category) for r in rows if r.persona == persona.name}
        assert ("investment_out", "Investments & Savings") in kinds
        assert ("investment_in", "Investment Redemption") in kinds
        assert ("income", REFUND_CATEGORY) in kinds


def test_person_to_person_payees_are_ids_not_names():
    transfers = [r.description for r in generate_history() if r.category == "Transfers" and r.txn_type == "expense"]
    assert transfers and all(re.fullmatch(r"UPI/P2P/PAYEE-\d{4}", d) for d in transfers)
