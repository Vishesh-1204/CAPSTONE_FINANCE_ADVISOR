# tests/test_evaluation.py
#
# Keeps the evaluation harness from silently breaking: runs it offline
# (--llm none) on a tiny synthetic dataset and checks the metrics CSV and a few
# values that are known by construction. Also unit-tests the metric formulas.

import csv
from pathlib import Path

import pytest

from evaluation.evaluate_categorizer import run_evaluation
from evaluation.metrics import binary_precision_recall, classification_metrics, override_rate
from evaluation.parsing_eval import evaluate_parsing

TINY_DATASET = """description,amount,category,direction,is_person,phase
Swiggy order dinner,450,Food & Dining,debit,0,1
NACH/HDFC MF SIP/FOLIO 1234567,5000,Investments & Savings,debit,0,1
SALARY CREDIT SEP,90000,Salary,credit,0,1
UPI/412345678901/RAHUL KUMAR/rahul.k99@okhdfcbank,600,Transfers,debit,1,1
KRISHNA TIFFIN CENTRE,150,Food & Dining,debit,0,1
KRISHNA TIFFIN CENTRE,160,Food & Dining,debit,0,2
UPI/512345678901/RAJ MESS/rajmess@okaxis,3000,Food & Dining,debit,0,1
UPI/612345678901/MONTHLY THALI/rajmess@okaxis,3000,Food & Dining,debit,0,2
"""


def _read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _value(records: list[dict], config: str, metric: str, subject: str = "") -> float:
    matches = [r for r in records if r["config"] == config and r["metric"] == metric and r["subject"] == subject]
    assert len(matches) == 1, (config, metric, subject)
    return float(matches[0]["value"])


@pytest.fixture(scope="module")
def records(tmp_path_factory) -> list[dict]:
    folder = tmp_path_factory.mktemp("evaluation")
    dataset = folder / "tiny.csv"
    dataset.write_text(TINY_DATASET, encoding="utf-8")
    path = run_evaluation(dataset, configs=["full", "memory_off", "payee_rules_off", "person_guard_off"],
                          out_dir=folder, quiet=True)
    assert path.with_suffix(".json").exists()
    return _read(path)


def test_csv_has_the_long_format_columns(records):
    assert set(records[0]) == {"run_id", "timestamp", "llm_mode", "provider", "config", "subject", "metric", "value"}
    assert {r["llm_mode"] for r in records} == {"none"}


def test_the_agent_learns_between_phases(records):
    # Phase 2: the tiffin repeat is answered by MEMORY, the new Raj Mess text by the PAYEE RULE
    assert _value(records, "full", "phase2_accuracy") == 1.0
    assert _value(records, "full", "phase2_memory_hit_rate") == 0.5
    assert _value(records, "full", "phase2_payee_rule_hit_rate") == 0.5
    assert _value(records, "memory_off", "phase2_memory_hit_rate") == 0.0
    assert _value(records, "payee_rules_off", "phase2_payee_rule_hit_rate") == 0.0


def test_privacy_leakage_is_zero_with_the_guard_and_visible_without(records):
    assert _value(records, "full", "privacy_leakage_count") == 0
    assert _value(records, "full", "person_guard_recall") == 1.0
    assert _value(records, "person_guard_off", "privacy_leakage_count") == 1


def test_budget_distortion_and_per_category_rows(records):
    assert _value(records, "investment_exclusion_off", "budget_distortion_pct") > 0
    assert _value(records, "full", "support", subject="Food & Dining") == 5


def test_parsing_metrics_cover_every_fixture(records):
    files = {r["subject"] for r in records if r["config"] == "parsing"}
    assert {"hdfc_like.pdf", "sbi_like.pdf", "scanned.pdf", "statement.xlsx", "messy.csv"} <= files
    assert all(_value(records, "parsing", "parse_success", subject=f) == 1.0 for f in files)


def test_parsing_eval_finds_the_planted_balance_error():
    metrics = evaluate_parsing()
    assert metrics["sbi_like.pdf"]["balance_mismatches_found"] == metrics["sbi_like.pdf"]["balance_mismatches_expected"] == 1
    assert metrics["messy.csv"]["narration_fidelity"] == 1.0


# ------------------------------------------------------------------ metric formulas


def test_classification_metrics_count_no_answer_as_wrong():
    result = classification_metrics(["A", "A", "B", "B"], ["A", None, "B", "A"])
    assert result["accuracy"] == 0.5 and result["coverage"] == 0.75
    assert result["accuracy_when_answered"] == pytest.approx(2 / 3)
    assert result["per_category"]["B"]["recall"] == 0.5 and result["per_category"]["A"]["precision"] == 0.5


def test_binary_precision_recall():
    assert binary_precision_recall([True, True, False, False], [True, False, True, False]) == (0.5, 0.5)
    assert binary_precision_recall([False], [False]) == (1.0, 1.0)       # nothing to find, nothing flagged


def test_override_rate_ignores_rows_the_agent_never_answered():
    overall, per_category = override_rate([("A", "A"), ("A", "B"), (None, "B"), ("B", "B")])
    assert overall == pytest.approx(1 / 3)
    assert per_category == {"A": 0.0, "B": 0.5}
