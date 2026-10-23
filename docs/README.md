# Documentation

Docs for the backend as of **Phase 5** (Financial Forecast Agent) on top of **Phase 4** (Budget Planning
Agent) and **Phase 3B v3** (Expense Intelligence Agent: statement ingestion, investment flows, manual
override, evaluation), plus the frontend Test Console (every page built).

Read in this order if you are new to the project:

| # | Document | What you learn |
|---|---|---|
| 1 | [architecture.md](architecture.md) | The layers, the folder map, and two flow diagrams (upload → categorize → review) |
| 2 | [database.md](database.md) | Every table and column, indexes, migrations 0001-0004 |
| 3 | [api.md](api.md) | Every endpoint: request, response, errors, examples |
| 4 | [testing.md](testing.md) | How to run tests, what each test file covers, manual test checklist |
| 5 | `modules/` | One page per module (below) |

## Module pages

| Module | Page | Main code |
|---|---|---|
| Auth | [modules/auth.md](modules/auth.md) | `api/routes/auth.py`, `services/auth_service.py`, `core/security.py` |
| Statement ingestion | [modules/statement-ingestion.md](modules/statement-ingestion.md) | `services/statement_parsers/`, `services/statement_import_service.py` |
| Transaction types & investments | [modules/transaction-types.md](modules/transaction-types.md) | `services/narration_parser.py`, `services/txn_type_detector.py`, `core/merchant_dictionary.py` |
| Categorization agent | [modules/categorization.md](modules/categorization.md) | `services/categorization_service.py`, `agents/expense_categorizer.py`, `llm/` |
| Overrides & review queue | [modules/overrides-and-review.md](modules/overrides-and-review.md) | `services/category_override_service.py`, `services/payee_rule_service.py` |
| Monthly summary | [modules/summary.md](modules/summary.md) | `services/summary_service.py` |
| Evaluation (research) | [modules/evaluation.md](modules/evaluation.md) | `evaluation/`, `scripts/make_synthetic_statements.py` |
| Budget Planning Agent | [modules/budget-planning.md](modules/budget-planning.md) | `services/budget_service.py`, `services/budget_calculator.py`, `agents/budget_planner.py` |
| Budget evaluation (research) | [modules/budget-evaluation.md](modules/budget-evaluation.md) | `evaluation/evaluate_budget.py`, `scripts/make_synthetic_history.py` |
| Financial Forecast Agent | [modules/forecasting.md](modules/forecasting.md) | `services/forecast_service.py`, `services/forecasting/`, `agents/forecast_explainer.py` |
| Forecast evaluation (research) | [modules/forecast-evaluation.md](modules/forecast-evaluation.md) | `evaluation/evaluate_forecast.py`, `scripts/make_synthetic_panel.py` |
| Frontend Test Console | [modules/frontend-console.md](modules/frontend-console.md) | `frontend/` |

Design sources: [phase3b-v3-spec.md](phase3b-v3-spec.md) (Phase 3B v3), [phase4-spec.md](phase4-spec.md) (Phase 4)
and [phase5-spec.md](phase5-spec.md) (Phase 5).

## Conventions used in these docs

- Paths like `services/x.py` are relative to `backend/app/` unless they start with `backend/`, `frontend/` or `docs/`.
- Commands are for **Windows PowerShell, run from the repo root**, as in `CLAUDE.md`.
- All examples use **synthetic data**. Never paste a real statement, account number or name into an example, a test or an LLM prompt.
