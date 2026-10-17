# evaluation/parsing_eval.py
#
# Purpose: PARSING-quality metrics (spec section 9), measured on the synthetic
# statements in tests/fixtures/statements/ against ground_truth.json:
#
#   parse_success        1 if the file parsed as expected (or failed exactly as expected)
#   row_recall           share of true transactions found (same date, direction, amount)
#   narration_fidelity   share of narrations recovered EXACTLY (PDF line wraps can split words)
#   balance_pass_rate    share of checkable rows whose running balance added up
#   balance_mismatches   mismatches found vs expected (planted errors)
#
# Runs the real parsers and the pipeline's balance check; no database needed.

import json
from decimal import Decimal
from pathlib import Path

from app.services.statement_import_service import check_balances
from app.services.statement_parsers import StatementParseError, get_parser

DEFAULT_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "statements"


def _file_metrics(path: Path, spec: dict, truth_rows: list[dict]) -> dict:
    expected_error = spec.get("expect_error")
    try:
        result = get_parser(path.name).parse(path.read_bytes(), password=spec.get("password"))
    except StatementParseError as error:
        return {"layout": "n/a", "parse_success": float(error.status_code == expected_error)}
    if expected_error:
        return {"layout": result.layout, "parse_success": 0.0}

    found = {(r.date.date().isoformat(), r.direction, r.amount) for r in result.rows}
    truth_keys = [(t["date"], t["direction"], Decimal(t["amount"])) for t in truth_rows]
    row_recall = sum(1 for key in truth_keys if key in found) / len(truth_keys)
    narrations = {r.narration for r in result.rows}
    fidelity = sum(1 for t in truth_rows if t["narration"] in narrations) / len(truth_rows)

    mismatches = check_balances(result.rows, result.opening_balance)
    checked = [r for r in result.rows if r.balance_ok is not None]
    return {
        "layout": result.layout,
        "parse_success": float(
            len(result.rows) == spec["rows"] and result.invalid_rows == spec["invalid"] and result.layout == spec["layout"]
        ),
        "rows_found": float(len(result.rows)),
        "rows_invalid": float(result.invalid_rows),
        "row_recall": row_recall,
        "narration_fidelity": fidelity,
        "balance_rows_checked": float(len(checked)),
        "balance_pass_rate": sum(1 for r in checked if r.balance_ok) / len(checked) if checked else 0.0,
        "balance_mismatches_found": float(len(mismatches)),
        "balance_mismatches_expected": float(spec["balance_mismatches"]),
    }


def evaluate_parsing(fixtures_dir: Path = DEFAULT_FIXTURES) -> dict[str, dict]:
    """{file name: metrics} for every fixture listed in ground_truth.json."""
    truth = json.loads((fixtures_dir / "ground_truth.json").read_text(encoding="utf-8"))
    return {
        name: _file_metrics(fixtures_dir / name, spec, truth["transactions"])
        for name, spec in truth["files"].items()
    }
