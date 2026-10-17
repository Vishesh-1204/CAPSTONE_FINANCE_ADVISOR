# evaluation/evaluate_categorizer.py
#
# Purpose: MEASURE the Expense Intelligence Agent for the paper. Unlike v2
# (which called the rule function and the LLM directly), v3 runs the REAL
# pipeline - categorize_pending_transactions - on a throwaway in-memory SQLite
# database, so the numbers describe exactly what the app does:
#   lock > payee rules > memory > dictionary/keyword rules > person guard > LLM
#
# Protocol (simulates two months of use):
#   phase 1  import the phase-1 rows, categorize, then an "oracle" user corrects
#            every wrong row (apply_user_correction). Whether it also ticks
#            "remember this payee" is --oracle-remember: upi (default: only for
#            UPI payees, the realistic opt-in), always, or never
#   phase 2  import the phase-2 rows (repeats + new), categorize again
#            -> memory / payee-rule hit rates show whether the agent LEARNS
#
# Configurations (ablations):
#   full, rules_off, memory_off, payee_rules_off, person_guard_off, rules_only
# Extra measurements on `full`: budget distortion if investments were counted as
# spending (investment-exclusion off), override rate. Plus parsing metrics on
# the synthetic statements (parsing_eval.py).
#
# LLM modes:
#   --llm none  (default) free and offline: whatever reaches the LLM stage counts
#               as unanswered, but every string SENT is still recorded, so the
#               privacy-leakage count is exact even without an API key.
#   --llm real  the configured provider (Gemini free tier: SYNTHETIC data only).
#
# Run (inside Docker):
#   docker compose exec backend python -m evaluation.evaluate_categorizer
#   docker compose exec backend python -m evaluation.evaluate_categorizer --llm real --configs full,rules_only
#
# Output: evaluation/results/metrics_<timestamp>.csv in LONG format
#   (run_id, timestamp, llm_mode, provider, config, subject, metric, value)
#   - pivot it in Excel/pandas for the paper's tables.
#
# Fairness note: the rule keywords and this dataset were written by the same
# team, so rules look better here than on truly unseen data. Build the final
# benchmark from independently written (or anonymised real) descriptions.

import argparse
import csv
import json
import logging
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers every table)
from app.agents.expense_categorizer import CategorizationResult, ExpenseCategorizerAgent
from app.core.config import settings
from app.core.transaction_types import DEBIT, TXN_EXPENSE
from app.db.base_class import Base
from app.models.transaction import Transaction
from app.models.user import User
from app.services import categorization_service
from app.services.category_override_service import apply_user_correction
from app.services.narration_parser import extract_upi_id
from app.services.text_normalizer import normalize_description
from evaluation.metrics import binary_precision_recall, classification_metrics, override_rate
from evaluation.parsing_eval import evaluate_parsing

DEFAULT_DATASET = Path(__file__).parent / "labeled_transactions.csv"
RESULTS_DIR = Path(__file__).parent / "results"

# config name -> settings to override (rules_only additionally forces --llm none)
CONFIGS: dict[str, dict[str, object]] = {
    "full": {},
    "rules_off": {"CATEGORIZER_ENABLE_RULES": False},
    "memory_off": {"CATEGORIZER_ENABLE_MEMORY": False},
    "payee_rules_off": {"CATEGORIZER_ENABLE_PAYEE_RULES": False},
    "person_guard_off": {"CATEGORIZER_ENABLE_PERSON_GUARD": False},
    "rules_only": {},
}
NO_LLM_REASON = "evaluation: no LLM"
SOURCES = ("payee_rule", "memory", "rule", "person_guard", "llm", "unanswered")
ORACLE_POLICIES = ("upi", "always", "never")


# ------------------------------------------------------------------ data


@dataclass(frozen=True)
class LabeledRow:
    description: str
    amount: Decimal
    category: str
    direction: str
    is_person: bool
    phase: int


def load_dataset(path: Path, limit: int | None = None) -> list[LabeledRow]:
    from app.core.categories import ALL_CATEGORY_NAMES
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [
            LabeledRow(
                description=r["description"], amount=Decimal(r["amount"]), category=r["category"],
                direction=r.get("direction") or DEBIT, is_person=r.get("is_person", "0") == "1",
                phase=int(r.get("phase") or 1),
            )
            for r in csv.DictReader(handle)
        ]
    unknown = {r.category for r in rows} - set(ALL_CATEGORY_NAMES)
    if unknown:
        raise ValueError(f"Dataset has labels that are not real categories: {sorted(unknown)}")
    return rows[:limit] if limit else rows


# ------------------------------------------------------------------ LLM recorder


@dataclass
class LlmStats:
    calls: int = 0
    sent: list[str] = field(default_factory=list)          # every description sent to the LLM
    examples: list[str] = field(default_factory=list)      # every few-shot example text sent


class RecordingAgent:
    """Wraps the real agent (or nothing) and records exactly what would leave the server."""

    def __init__(self, stats: LlmStats, inner: ExpenseCategorizerAgent | None) -> None:
        self.stats, self.inner = stats, inner

    def categorize_batch(self, items, examples):
        self.stats.calls += 1
        self.stats.sent.extend(item.description for item in items)
        self.stats.examples.extend(text for text, _ in examples)
        if self.inner is None:   # offline mode: answer nothing useful, but keep the pipeline going
            return {item.item_id: CategorizationResult("Other", 0.0, NO_LLM_REASON, "llm") for item in items}
        return self.inner.categorize_batch(items, examples)


@contextmanager
def overridden_settings(values: dict[str, object]):
    """Temporarily changes settings (ablation switches) and always restores them."""
    old = {key: getattr(settings, key) for key in values}
    try:
        for key, value in values.items():
            setattr(settings, key, value)
        yield
    finally:
        for key, value in old.items():
            setattr(settings, key, value)


# ------------------------------------------------------------------ one configuration


def _new_session() -> Session:
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(email="eval@example.com", full_name="Evaluation", hashed_password="x"))
    session.commit()
    return session


def _insert(db: Session, rows: list[LabeledRow], start: datetime) -> list[Transaction]:
    transactions = [
        Transaction(
            user_id=1, description=row.description, amount=row.amount, direction=row.direction,
            transaction_date=start + timedelta(hours=index),
        )
        for index, row in enumerate(rows)
    ]
    db.add_all(transactions)
    db.commit()
    return transactions


def _source(tx: Transaction) -> str:
    if tx.category is None or tx.category_reasoning == NO_LLM_REASON:
        return "unanswered"
    if tx.category_reasoning == categorization_service.PERSON_GUARD_REASON:
        return "person_guard"
    return tx.category_source or "unanswered"


def _oracle_remembers(description: str, policy: str) -> bool:
    """Does the simulated user tick 'remember this payee' when correcting this row?"""
    return policy == "always" or (policy == "upi" and extract_upi_id(description) is not None)


def run_config(name: str, rows: list[LabeledRow], llm_mode: str, oracle_remember: str = "upi") -> dict:
    """Runs the two-phase protocol for one configuration and returns its metrics."""
    overrides = dict(CONFIGS[name])
    use_real_llm = llm_mode == "real" and name != "rules_only"
    if not use_real_llm:
        overrides["CATEGORIZER_BATCH_DELAY_SECONDS"] = 0.0    # nothing to rate-limit offline

    stats = LlmStats()
    inner = ExpenseCategorizerAgent() if use_real_llm else None

    def factory() -> RecordingAgent:
        return RecordingAgent(stats, inner)

    db = _new_session()
    predictions: dict[int, tuple[str | None, str]] = {}        # dataset index -> (prediction, source)
    started = time.perf_counter()
    with overridden_settings(overrides):
        for phase, month in ((1, 8), (2, 9)):
            indices = [i for i, r in enumerate(rows) if r.phase == phase]
            inserted = _insert(db, [rows[i] for i in indices], datetime(2026, month, 1, tzinfo=timezone.utc))
            categorization_service.categorize_pending_transactions(db, user_id=1, agent_factory=factory)
            for index, tx in zip(indices, inserted):
                db.refresh(tx)
                source = _source(tx)
                predictions[index] = (None if source == "unanswered" else tx.category, source)
            # The oracle user fixes every wrong answer (remembering the payee per policy)
            for index, tx in zip(indices, inserted):
                if predictions[index][0] != rows[index].category:
                    remember = _oracle_remembers(rows[index].description, oracle_remember)
                    apply_user_correction(db, 1, tx.id, rows[index].category, remember_payee=remember)
    seconds = time.perf_counter() - started

    transactions = db.query(Transaction).order_by(Transaction.id).all()
    metrics = _config_metrics(rows, predictions, stats, transactions, seconds)
    db.close()
    return metrics


def _config_metrics(
    rows: list[LabeledRow], predictions: dict[int, tuple[str | None, str]], stats: LlmStats,
    transactions: list[Transaction], seconds: float,
) -> dict:
    truths = [r.category for r in rows]
    preds = [predictions[i][0] for i in range(len(rows))]
    sources = Counter(predictions[i][1] for i in range(len(rows)))
    headline = classification_metrics(truths, preds)

    phase2 = [i for i, r in enumerate(rows) if r.phase == 2]
    phase2_metrics = classification_metrics([truths[i] for i in phase2], [preds[i] for i in phase2])
    phase2_sources = Counter(predictions[i][1] for i in phase2)

    debit_indices = [i for i, r in enumerate(rows) if r.direction == DEBIT]
    guard_precision, guard_recall = binary_precision_recall(
        [rows[i].is_person for i in debit_indices],
        [predictions[i][1] == "person_guard" for i in debit_indices],
    )
    sent = set(stats.sent) | set(stats.examples)
    leaked = sum(1 for r in rows if r.is_person and normalize_description(r.description) in sent)

    overall_override, _ = override_rate([(t.agent_category, t.category) for t in transactions])
    count = len(rows)
    result = {
        "rows": float(count),
        "accuracy": headline["accuracy"],
        "accuracy_when_answered": headline["accuracy_when_answered"],
        "coverage": headline["coverage"],
        "macro_f1": headline["macro_f1"],
        "phase2_accuracy": phase2_metrics["accuracy"],
        "llm_calls": float(stats.calls),
        "llm_calls_per_100_rows": 100 * stats.calls / count,
        "llm_strings_sent": float(len(stats.sent)),
        "seconds_per_row": seconds / count,
        "person_guard_precision": guard_precision,
        "person_guard_recall": guard_recall,
        "privacy_leakage_count": float(leaked),
        "override_rate": overall_override,
        "phase2_memory_hit_rate": phase2_sources["memory"] / len(phase2) if phase2 else 0.0,
        "phase2_payee_rule_hit_rate": phase2_sources["payee_rule"] / len(phase2) if phase2 else 0.0,
    }
    for source in SOURCES:
        result[f"share_{source}"] = sources[source] / count
    return {"metrics": result, "per_category": headline["per_category"], "transactions": transactions}


def budget_distortion(transactions: list[Transaction]) -> dict[str, float]:
    """
    'Investment-exclusion off' ablation: what the expense total would be if every
    debit (SIPs, broker top-ups, self-transfers) counted as spending.
    """
    debits = [t for t in transactions if t.direction == DEBIT]
    correct = sum((t.amount for t in debits if t.txn_type == TXN_EXPENSE), Decimal("0"))
    naive = sum((t.amount for t in debits), Decimal("0"))
    return {
        "expense_total_correct": float(correct),
        "expense_total_if_investments_counted": float(naive),
        "budget_distortion_pct": float((naive - correct) / correct * 100) if correct else 0.0,
    }


# ------------------------------------------------------------------ output


def _write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["run_id", "timestamp", "llm_mode", "provider", "config", "subject", "metric", "value"]
        )
        writer.writeheader()
        writer.writerows(records)


def _print_report(results: dict[str, dict], parsing: dict[str, dict], distortion: dict[str, float] | None) -> None:
    columns = [
        ("accuracy", "acc"), ("coverage", "cover"), ("macro_f1", "mF1"), ("phase2_accuracy", "acc(p2)"),
        ("phase2_memory_hit_rate", "mem(p2)"), ("phase2_payee_rule_hit_rate", "rule(p2)"),
        ("llm_calls_per_100_rows", "LLM/100"), ("person_guard_recall", "guardR"), ("privacy_leakage_count", "leaks"),
    ]
    print("\n=== CATEGORIZATION (real pipeline, two-phase protocol) ===")
    print(f"{'config':17}" + "".join(f"{label:>9}" for _, label in columns))
    for name, result in results.items():
        m = result["metrics"]
        print(f"{name:17}" + "".join(f"{m[key]:>9.3f}" if key != "privacy_leakage_count" else f"{int(m[key]):>9}"
                                     for key, _ in columns))
    if distortion:
        print(f"\nBudget distortion if investments/transfers counted as spending: "
              f"+{distortion['budget_distortion_pct']:.1f}% "
              f"({distortion['expense_total_correct']:,.0f} -> {distortion['expense_total_if_investments_counted']:,.0f})")
    print("\n=== PARSING (synthetic statements) ===")
    for name, m in parsing.items():
        print(f"{name:16} layout={m['layout']:10} success={m['parse_success']:.0f}"
              + (f" recall={m['row_recall']:.2f} narration={m['narration_fidelity']:.2f} "
                 f"balance_pass={m['balance_pass_rate']:.2f} mismatches={int(m['balance_mismatches_found'])}"
                 if "row_recall" in m else ""))


def run_evaluation(
    dataset: Path = DEFAULT_DATASET, configs: list[str] | None = None, llm_mode: str = "none",
    limit: int | None = None, out_dir: Path = RESULTS_DIR, include_parsing: bool = True, quiet: bool = False,
    oracle_remember: str = "upi",
) -> Path:
    """Runs everything and writes the metrics CSV (+ JSON). Returns the CSV path."""
    rows = load_dataset(dataset, limit)
    configs = configs or list(CONFIGS)
    provider = ExpenseCategorizerAgent().provider_name if llm_mode == "real" else "none"
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    results = {name: run_config(name, rows, llm_mode, oracle_remember) for name in configs}
    distortion = budget_distortion(results["full"]["transactions"]) if "full" in results else None
    parsing = evaluate_parsing() if include_parsing else {}

    def record(config: str, subject: str, metric: str, value: float) -> dict:
        return {"run_id": run_id, "timestamp": stamp, "llm_mode": llm_mode, "provider": provider,
                "config": config, "subject": subject, "metric": metric, "value": round(float(value), 6)}

    records: list[dict] = []
    for name, result in results.items():
        records += [record(name, "", metric, value) for metric, value in result["metrics"].items()]
        for category, scores in result["per_category"].items():
            records += [record(name, category, metric, scores[metric]) for metric in ("precision", "recall", "f1", "support")]
    if distortion:
        records += [record("investment_exclusion_off", "", metric, value) for metric, value in distortion.items()]
    for file_name, metrics in parsing.items():
        records += [record("parsing", file_name, metric, value) for metric, value in metrics.items() if metric != "layout"]

    out_path = out_dir / f"metrics_{run_id}.csv"
    _write_csv(out_path, records)
    summary = {name: r["metrics"] for name, r in results.items()}
    out_path.with_suffix(".json").write_text(
        json.dumps({"run_id": run_id, "llm_mode": llm_mode, "provider": provider, "dataset_rows": len(rows),
                    "oracle_remember": oracle_remember,
                    "configs": summary, "budget_distortion": distortion, "parsing": parsing}, indent=2),
        encoding="utf-8",
    )
    if not quiet:
        print(f"Dataset: {dataset.name} ({len(rows)} rows) | LLM: {llm_mode} ({provider}) | "
              f"oracle remembers: {oracle_remember} | configs: {configs}")
        _print_report(results, parsing, distortion)
        print(f"\nSaved: {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the expense categorizer and the statement parsers.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--configs", default=",".join(CONFIGS), help=f"comma-separated subset of: {', '.join(CONFIGS)}")
    parser.add_argument("--llm", choices=("none", "real"), default="none")
    parser.add_argument("--limit", type=int, default=None, help="only use the first N rows (quick test)")
    parser.add_argument("--skip-parsing", action="store_true")
    parser.add_argument("--oracle-remember", choices=ORACLE_POLICIES, default="upi",
                        help="when the simulated user ticks 'remember this payee'")
    args = parser.parse_args()

    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    unknown = set(configs) - set(CONFIGS)
    if unknown:
        parser.error(f"unknown configs: {sorted(unknown)}")
    logging.basicConfig(level=logging.WARNING)
    run_evaluation(args.dataset, configs, args.llm, args.limit, include_parsing=not args.skip_parsing,
                   oracle_remember=args.oracle_remember)


if __name__ == "__main__":
    main()
