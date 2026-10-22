# Module: Evaluation harness (research)

> This page covers the **categorizer** evaluation (Phase 3B). The Budget Planning Agent has its own
> harness: [budget-evaluation.md](budget-evaluation.md).

**Files:** `backend/evaluation/evaluate_categorizer.py`, `evaluation/metrics.py`, `evaluation/parsing_eval.py`,
`evaluation/labeled_transactions.csv`, `evaluation/results/` (gitignored), `backend/scripts/make_synthetic_statements.py`,
`backend/tests/fixtures/statements/`

## Why it exists
To produce the numbers for the IEEE paper and to prove each design choice earns its place (ablations).
It runs the **real** pipeline (`categorize_pending_transactions`) on a throwaway in-memory SQLite
database, so the numbers describe what the app actually does.

## Protocol
- **Dataset:** `labeled_transactions.csv`, 114 synthetic rows with true categories, including credits and investments.
- **Phase 1:** import phase-1 rows, categorize, then an "oracle" user corrects every wrong row
  (`apply_user_correction`). `--oracle-remember upi` (default) ticks "remember payee" only for UPI payees;
  `always` / `never` are alternatives.
- **Phase 2:** import repeats + new rows, categorize again. Memory and payee-rule hit rates show whether the agent **learns**.
- **Configurations (ablations):** `full`, `rules_off`, `memory_off`, `payee_rules_off`, `person_guard_off`, `rules_only`.
- **LLM modes:** `--llm none` (default; free, offline: whatever reaches the LLM is "unanswered", but every
  string *sent* is recorded, so privacy leakage is exact) or `--llm real` (configured provider; synthetic data only).

## Metrics
Accuracy, coverage, accuracy-when-answered, macro-F1, per-category precision/recall/F1, LLM calls per 100
rows, strings sent to the LLM, seconds per row, share of each source, phase-2 accuracy and hit rates,
person-guard precision/recall, privacy-leakage count, override rate, budget distortion
(expense total if investments were counted), and parsing metrics per synthetic file
(parse success, row recall, narration fidelity, balance pass rate, mismatches found vs planted).

Formulas are hand-written in `metrics.py` (no scikit-learn dependency).

## Latest offline results (run `20261015_114228`, `--llm none`, 114 rows)

| Config | Accuracy | Macro-F1 | LLM calls /100 rows | Strings sent to LLM | Privacy leakage | Phase-2 accuracy |
|---|---|---|---|---|---|---|
| full | 0.895 | 0.951 | 0.88 | 12 | 1 | 1.000 |
| rules_off | 0.360 | 0.411 | 4.39 | 73 | 1 | 0.917 |
| memory_off | 0.868 | 0.929 | 1.75 | 15 | 1 | 0.750 |
| payee_rules_off | 0.886 | 0.947 | 0.88 | 12 | 1 | 0.917 |
| person_guard_off | 0.851 | 0.931 | 1.75 | 17 | **7** | 0.917 |
| rules_only | 0.895 | 0.951 | 0.88 | 12 | 1 | 1.000 |

Budget distortion if investments were counted as spending: **+38.1%** (₹2,92,570 → ₹4,04,070).
Override rate (full): 0.061. Person guard: precision 1.0, recall 0.857.

How to read this honestly:
- With `--llm none`, accuracy is a **lower bound**: the 10.5% of rows that reached the LLM count as wrong.
  `full` and `rules_only` are identical for that reason. Run `--llm real` for the hybrid numbers.
- `rules_off` shows the deterministic layers carry most of the accuracy and cut LLM traffic by ~5×.
- `person_guard_off` raises leakage from 1 to 7 strings: the guard's measurable privacy effect.
- The 1 leak with the guard on is a real limitation to report (guard recall < 1).

## How to run
```powershell
docker compose exec backend python -m evaluation.evaluate_categorizer              # offline
docker compose exec backend python -m evaluation.evaluate_categorizer --llm real  # uses your provider
docker compose exec backend python -m scripts.make_synthetic_statements           # regenerate fixtures
```
Output: `backend/evaluation/results/metrics_<run_id>.csv` (long format: run, config, subject, metric, value)
and `.json`. The folder is gitignored; copy the CSV you cite into the paper's materials.

## Common mistakes
- Using real transactions in the labeled set (privacy; the free Gemini tier may train on inputs).
- Comparing runs with different `--llm` modes or oracle policies.
- Reporting accuracy without coverage.

## How to test
`tests/test_evaluation.py` (9) runs the harness offline on a tiny dataset and checks the CSV and values known
by construction, plus the metric formulas.

## Next
A `--llm real` run for the paper's hybrid column; budget forecast error vs rule baselines in Phase 5.
