# evaluation/evaluate_categorizer.py
#
# Purpose: MEASURE how good the categorizer is. Without numbers you can only
# say "it seems to work"; with them you can say "hybrid reaches 93% accuracy
# vs 78% for rules alone" - which is what an IEEE paper needs.
#
# It compares three configurations on a hand-labeled CSV:
#   rules   - keyword rules only (the rule-based BASELINE)
#   llm     - the LLM alone, no rules
#   hybrid  - rules first, LLM for whatever the rules cannot decide (what the app does)
#
# Run (from the backend folder, inside Docker):
#   docker compose exec backend python -m evaluation.evaluate_categorizer
#   docker compose exec backend python -m evaluation.evaluate_categorizer --configs rules
#   docker compose exec backend python -m evaluation.evaluate_categorizer --limit 20
#
# Fairness note for the paper: the rule keywords were written by looking at
# typical Indian merchants, so rules look better here than they would on a
# truly unseen dataset. Build your final benchmark from REAL (anonymised) or
# independently written descriptions, and keep it separate from the rules.

import argparse
import csv
import json
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from app.agents.expense_categorizer import ExpenseCategorizerAgent, PendingItem
from app.core.categories import CATEGORY_NAMES
from app.core.config import settings
from app.services.rule_based_categorizer import rule_categorize

DEFAULT_DATASET = Path(__file__).parent / "labeled_transactions.csv"
RESULTS_DIR = Path(__file__).parent / "results"


def load_dataset(path: Path, limit: int | None = None) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [
            {"description": r["description"], "amount": float(r["amount"]), "truth": r["category"]}
            for r in csv.DictReader(handle)
        ]
    unknown = {r["truth"] for r in rows} - set(CATEGORY_NAMES)
    if unknown:
        raise ValueError(f"Dataset has labels that are not real categories: {sorted(unknown)}")
    return rows[:limit] if limit else rows


def run_llm(agent: ExpenseCategorizerAgent, rows: list[dict], indices: list[int]) -> tuple[dict[int, str], int]:
    """Classifies rows[indices] in batches. Returns ({row_index: category}, llm_calls)."""
    predictions: dict[int, str] = {}
    calls = 0
    batch_size = settings.CATEGORIZER_BATCH_SIZE
    for start in range(0, len(indices), batch_size):
        if start:
            time.sleep(settings.CATEGORIZER_BATCH_DELAY_SECONDS)
        chunk = indices[start:start + batch_size]
        items = [PendingItem(item_id=n, description=rows[i]["description"], amount=rows[i]["amount"]) for n, i in enumerate(chunk)]
        results = agent.categorize_batch(items, examples=[])
        calls += 1
        for n, i in enumerate(chunk):
            if n in results:
                predictions[i] = results[n].category
    return predictions, calls


def evaluate_config(name: str, rows: list[dict], agent: ExpenseCategorizerAgent | None) -> dict:
    """Returns predictions plus counters for one configuration."""
    started = time.perf_counter()
    predictions: dict[int, str | None] = {}
    decided_by_rule: set[int] = set()
    llm_calls = 0

    if name in ("rules", "hybrid"):
        for i, row in enumerate(rows):
            match = rule_categorize(row["description"])
            if match:
                predictions[i] = match.category
                decided_by_rule.add(i)

    if name in ("llm", "hybrid"):
        todo = [i for i in range(len(rows)) if i not in decided_by_rule]
        llm_predictions, llm_calls = run_llm(agent, rows, todo)
        predictions.update(llm_predictions)

    return {
        "name": name,
        "predictions": [predictions.get(i) for i in range(len(rows))],   # None = no answer
        "rule_decided": len(decided_by_rule),
        "llm_calls": llm_calls,
        "seconds": time.perf_counter() - started,
    }


def macro_f1(truths: list[str], preds: list[str | None]) -> tuple[float, dict[str, dict[str, float]]]:
    """Macro-F1 over the categories that appear in the ground truth, plus per-category precision/recall."""
    per_category: dict[str, dict[str, float]] = {}
    for category in sorted(set(truths)):
        tp = sum(1 for t, p in zip(truths, preds) if t == category and p == category)
        fp = sum(1 for t, p in zip(truths, preds) if t != category and p == category)
        fn = sum(1 for t, p in zip(truths, preds) if t == category and p != category)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_category[category] = {"precision": precision, "recall": recall, "f1": f1, "support": truths.count(category)}
    return sum(c["f1"] for c in per_category.values()) / len(per_category), per_category


def summarize(result: dict, rows: list[dict]) -> dict:
    truths = [r["truth"] for r in rows]
    preds = result["predictions"]
    answered = [(t, p) for t, p in zip(truths, preds) if p is not None]
    correct = sum(1 for t, p in answered if t == p)
    f1, per_category = macro_f1(truths, preds)
    return {
        "config": result["name"],
        "rows": len(rows),
        "answered": len(answered),
        "coverage": len(answered) / len(rows),
        "accuracy_overall": correct / len(rows),                              # unanswered counts as wrong
        "accuracy_when_answered": correct / len(answered) if answered else 0.0,
        "macro_f1": f1,
        "llm_calls": result["llm_calls"],
        "rows_decided_by_rules": result["rule_decided"],
        "seconds": round(result["seconds"], 2),
        "seconds_per_row": round(result["seconds"] / len(rows), 3),
        "per_category": per_category,
    }


def print_report(summaries: list[dict], results: list[dict], rows: list[dict]) -> None:
    print("\n=== SUMMARY ===")
    header = f"{'config':8} {'acc(all)':>9} {'acc(ans)':>9} {'coverage':>9} {'macroF1':>8} {'LLM calls':>10} {'sec/row':>8}"
    print(header)
    print("-" * len(header))
    for s in summaries:
        print(f"{s['config']:8} {s['accuracy_overall']:>9.1%} {s['accuracy_when_answered']:>9.1%} {s['coverage']:>9.1%} "
              f"{s['macro_f1']:>8.3f} {s['llm_calls']:>10} {s['seconds_per_row']:>8}")

    best = max(summaries, key=lambda s: s["macro_f1"])
    print(f"\n=== PER-CATEGORY ({best['config']}) ===")
    for category, m in best["per_category"].items():
        print(f"{category:24} P={m['precision']:.2f} R={m['recall']:.2f} F1={m['f1']:.2f} (n={int(m['support'])})")

    for result in results:
        wrong = [(rows[i], p) for i, p in enumerate(result["predictions"]) if p != rows[i]["truth"]]
        if not wrong:
            continue
        print(f"\n=== MISTAKES ({result['name']}) ===")
        for row, pred in wrong:
            print(f"  {row['description'][:44]:44} truth={row['truth']:22} predicted={pred or '(no answer)'}")
        confusions = Counter((r["truth"], p) for r, p in wrong)
        print("  Most common confusions:", confusions.most_common(3))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the expense categorizer on a labeled CSV.")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--configs", default="rules,llm,hybrid", help="comma-separated: rules,llm,hybrid")
    parser.add_argument("--limit", type=int, default=None, help="only use the first N rows (quick test)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    rows = load_dataset(args.dataset, args.limit)
    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    print(f"Dataset: {args.dataset.name} ({len(rows)} rows)  |  configs: {configs}")

    agent = None
    if any(c in ("llm", "hybrid") for c in configs):
        agent = ExpenseCategorizerAgent()
        print(f"LLM provider: {agent.provider_name}")

    results = [evaluate_config(c, rows, agent) for c in configs]
    summaries = [summarize(r, rows) for r in results]
    print_report(summaries, results, rows)

    RESULTS_DIR.mkdir(exist_ok=True)
    out = RESULTS_DIR / f"eval_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps({"provider": getattr(agent, "provider_name", None), "summaries": summaries}, indent=2))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
