# Testing

## Run the tests

```powershell
docker compose exec backend python -m pytest tests -q                                  # everything (745)
docker compose exec backend python -m pytest tests/test_statement_parsers.py -q        # one file
docker compose exec backend python -m pytest tests -q -k "password"                    # by name
```

Tests need **no Postgres, no network and no API key**:
- the database is a fresh in-memory SQLite per test (`tests/conftest.py`, users 1 and 2 pre-created);
- the LLM is a fake chain/agent that returns canned text and records what it was sent;
- HTTP tests use FastAPI's `TestClient` and override `get_current_user`, so no login is needed.

`tests/test_migrations.py` is the exception that touches Alembic: it applies all migrations to an empty
database and compares the result with the models (drift check).

Last full run: **745 passed** (Phase 6A + 6B, on `feature/vishesh-investing`), plus the live smoke
test below: 16/16 steps passed.

## What each test file covers

| Module | File | Tests | Covers |
|---|---|---|---|
| Migrations | `test_migrations.py` | 7 | migrations 0001-0005 apply on an empty DB, 0004 and 0005 keep existing data, 0005 downgrades to 0004 cleanly and upgrades again; models and migrations match |
| Ingestion | `test_statement_parsers.py` | 27 | XLSX, HDFC-like and SBI-like PDFs, password right/wrong/missing, password never in logs/errors, scanned → 422, balance check, cross-format duplicates |
| Ingestion | `test_statement_import.py` | 22 | CSV through the shared pipeline: header detection, amount/date formats, credits imported, duplicates, import batches |
| Ingestion (HTTP) | `test_statements_api.py` | 6 | upload summary, import batches, deprecated `/upload-csv` alias |
| Types | `test_txn_type_detector.py` | 36 | every investment/income/transfer case from spec §6, `to_money` |
| Types | `test_narration_parser.py` | 16 | channel, UPI ID, payee key extraction |
| Types | `test_text_normalizer.py` | 9 | description normalization |
| Categorization | `test_categorization_service.py` | 19 | priority order, grouping, memory, failures leave rows uncategorized |
| Categorization | `test_rule_based_categorizer.py` | 16 | keyword rules, precision-over-coverage |
| Categorization | `test_expense_categorizer.py` | 14 | prompt/JSON parsing, retries, provider fallback (fake chains) |
| Privacy | `test_person_payee_guard.py` | 14 | person detection; no name, phone, UPI ID or reference reaches the LLM |
| Overrides | `test_category_overrides.py` | 14 | locks, `agent_category`, apply-to-similar, payee-rule priority, unlock, bulk, direction checks |
| API | `test_transactions_api.py` | 8 | end-to-end HTTP: create, list, categorize, correct |
| API | `test_transactions_api_v3.py` | 8 | filters + paging envelope, summary, bulk, unlock, extended PATCH, payee rules |
| API | `test_categories_api.py` | 1 | `GET /api/categories/` returns the lists from `core/categories.py` |
| Evaluation | `test_evaluation.py` | 9 | harness runs offline, CSV written, known values, metric formulas |
| Budgets | `test_budget_calculator.py` | 15 | baseline weights sum to 1, spendable, trim, scale-down, infeasible, never above spendable, savings never budgeted |
| Budgets | `test_month_utils.py` | 12 | month bounds, December rollover, lookback window, bad months |
| Budgets | `test_explanation_guard.py` | 21 | ₹ / Rs. / INR extraction, tolerance, invented amounts; Indian digit grouping, percentages, lakh / crore / k / L detection |
| Budgets | `test_budget_planner.py` | 23 | templates, JSON parsing, guard replacement, provider fallback, payload has plan facts only |
| Budgets | `test_budget_service.py` | 33 | expenses only, window, isolation, overrides survive regeneration, reset, data quality, LLM down/unfaithful, privacy, status, income suggestion, average essential spending (budget / history / none) |
| Budgets (HTTP) | `test_budgets_api.py` | 30 | every profile/budget endpoint, 404 on another user's budget, 422 validation |
| Budget evaluation | `test_synthetic_history.py` | 6 | same seed → same rows, real labels, 3 personas x 6 months, non-spending rows, no names |
| Budget evaluation | `test_budget_evaluation.py` | 13 | harness runs offline, every config reported, B1 beats B0 on MAE, distortion > 0, guard catches a fake LLM, metric formulas |
| Forecast (pure) | `test_forecast_models.py` | 8 | naive, moving average, seasonal naive, exponential smoothing, the model registry |
| Forecast (pure) | `test_forecast_metrics.py` | 5 | MAE, RMSE, MAPE (zeros skipped), sMAPE |
| Forecast (pure) | `test_forecast_backtest.py` | 24 | no peeking at the future, selection, min folds, horizon-sum errors, Student-t / empirical / conformal intervals |
| Forecast (pure) | `test_recurrence_detector.py` | 20 | rent/salary/SIP detection, changing bills rejected, salary hike, one-off deposit, busy shop rejected (frequency check), projection dates |
| Forecast (pure) | `test_forecast_horizon.py` | 9 | days to weighted calendar months, year rollover, zero-weight months |
| Forecast (pure) | `test_forecast_variable.py` | 5 | fallback model + low confidence, backtest selection, trend following |
| Forecast (pure) | `test_cashflow_projector.py` | 9 | scheduled flows on their dates, band growth, cash-crunch date, prorated first month |
| Forecast | `test_forecast_data_preparation.py` | 24 | complete months only, rent not counted twice, SIP/refund/transfer not spending, opening balance order, income source, isolation |
| Forecast | `test_forecast_explainer.py` | 15 | template, one LLM call, guard replacement, provider fallback, payload privacy |
| Forecast (HTTP) | `test_forecast_api.py` | 32 | every endpoint, 404 on another user's run, 400/422 cases, LLM down/unfaithful, same `as_of` gives the same forecast |
| Forecast evaluation | `test_synthetic_panel.py` | 12 | same seed gives same rows, 30 users x 24 months, running balance, recurring ground truth, salary hike, festival, medical bill, crunch events, no names |
| Investing (pure) | `test_risk_scoring.py` | 9 | tolerance and capacity scores, emergency-fund cap, the safer category wins, limiting factor |
| Investing (pure) | `test_allocation.py` | 25 | splits sum to 1, horizon equity cap, drift threshold, rebalance amounts balance |
| Investing (pure) | `test_portfolio_stats.py` | 4 | weighted return, diversification lowers volatility |
| Investing (pure) | `test_sip_planner.py` | 11 | future value, required SIP, emergency top-up, safety factor, months to goal |
| Investing (pure) | `test_monte_carlo.py` | 6 | same seed same result, ordered percentiles, zero volatility = closed form |
| Investing (pure) | `test_claims_guard.py` | 13 | promises caught (incl. curly apostrophe), balanced text passes, negations flagged on purpose |
| Investing | `test_investing_data_model.py` | 19 | schema Literals = the maths' lists, bounds, duplicate holdings, disclaimer on every response, CHECK and UNIQUE rules, plan defaults |
| Investing | `test_investing_service.py` | 28 | lower-band surplus formula, 30-day run chosen, forecast_missing / stale, capacity vs tolerance limit, emergency top-up, goal cap, horizon cap, isolation, reproducible plans, LLM log |
| Investing | `test_investment_explainer.py` | 25 | Indian-grouped formatting, template passes every guard, invented amount / percentage / lakh rejected, claims rejected and counted, one call, provider fallback |
| Investing (HTTP) | `test_investing_api.py` | 34 | every route needs a login (real auth), 422 validation, 409 without a profile, user isolation, body `user_id` ignored, disclaimer everywhere, simulate not stored |
| Investing evaluation | `test_investing_evaluation.py` | 15 | rule labels, personas reproducible, every method and ablation, evaluation P = the real service, panel replay monotonic, guard cases, deterministic report |
| Forecast evaluation | `test_forecast_evaluation.py` | 14 | harness runs offline, every method x horizon, no leakage (future changed, no forecast changes), service = pure numbers, statement opening balance, guard catches a fake LLM, F6.1 ablations, metric formulas |

Fixtures: `backend/tests/fixtures/statements/` (synthetic; regenerate with
`docker compose exec backend python -m scripts.make_synthetic_statements`). PDF/XLSX fixtures are marked
binary in `.gitattributes` so Git never changes their bytes.

## Frontend checks

```powershell
cd frontend
npm run lint
npm run build        # also type-checks every page
```

## Automated smoke test (live API)

`backend/scripts/smoke_test.py` checks the **running** stack end to end over real HTTP, with the real
Postgres database, migrations and router wiring (the pytest suite cannot see those). Run it after
`docker compose up`:

```powershell
docker compose exec backend python -m scripts.smoke_test
docker compose exec backend python -m scripts.smoke_test --base-url http://127.0.0.1:8000   # explicit URL
```

It runs 16 steps and prints one `PASS`/`FAIL` line per step. It stops at the first failure (later
steps depend on earlier ones) and exits `1`. Exit `0` means every step passed:

| # | Step | What it checks |
|---|---|---|
| 1 | health | `GET /health` answers |
| 2-3 | register, login | a NEW user each run (`smoke-<random>@example.com`), bearer token |
| 4 | upload CSV | 27 synthetic rows (2 full past months + day 1 of this month), 0 invalid |
| 5 | categorize | every row categorized (all match keyword rules, so no LLM call), 0 failed |
| 6 | save profile | income 60,000, 20% target → spendable 48,000 |
| 7 | income suggestion | the salary average, 60,000 |
| 8 | generate (next month) | `method=history`, limits ≤ spendable, the SIP is NOT budgeted |
| 9 | status | every budgeted category `ok` (nothing spent next month yet) |
| 10 | override | `Food & Dining` set to half the agent limit → `source=user` |
| 11-12 | regenerate | the override keeps its id, value and `source=user` |
| 13 | forecast without a balance | **400** (the smoke CSV has no balance column) |
| 14 | 30-day forecast | typed `opening_balance` used, 30 trajectory days, end = opening + net cash flow, end inside its band, the SIP is not a fixed expense |
| 15 | forecast reads | `latest` is that run, `history` lists it, `recurring` answers |
| 16 | investing | profile and holdings saved; the plan uses THIS user's 30-day forecast, its split sums to 1, SIP ≤ capacity, p10 ≤ p50 ≤ p90, disclaimer on every response, `latest` is that plan, `simulate` stores nothing |

Notes: it uses the standard library only (`urllib`), so it needs no extra dependency. Each run leaves one
synthetic user in the dev DB, which is harmless. With an LLM key in `.env`, the budget explanation makes
one real LLM call per generation (`llm_used=True`). Only category names and totals are sent. Without a key
the template is used and the test still passes.

## Manual end-to-end checklist (Swagger or the Test Console)

1. `GET /health` → `{"status": "ok", ...}`.
2. Register a synthetic user, log in, **Authorize**.
3. Upload `messy.csv` → `rows_invalid: 1`; upload it again → all rows duplicates.
4. Upload `protected.pdf` without a password (400), with a wrong one (400), with `test1234` (201).
5. Upload `scanned.pdf` → 422 with the scanned-PDF message.
6. Upload `sbi_like.pdf` → `balance_mismatches: 1`.
7. `POST /api/transactions/categorize` → check the per-source counts; without an API key the remaining rows are `failed` with a message (never "Other").
8. `GET /api/transactions/?status=needs_review` → person-guarded rows are there as Transfers.
9. `PATCH` one row with `apply_to_similar` and `remember_payee` → `similar_updated`, `payee_rule`; the row is `user_locked`.
10. `POST /api/transactions/categorize?reset_ai_results=true` → the locked row is unchanged.
11. `GET /api/transactions/summary?month=<fixture month>` → investments are not in `expense_total`.
12. `GET /api/payee-rules/`, then `DELETE` it → 204.
13. `GET /api/profile/financial/suggestion?month=<month after the fixture>` → the salary average (refunds excluded).
14. `PUT /api/profile/financial`, then `POST /api/budgets/generate?month=...` → limits sum ≤ `spendable_income`; `data_quality` filled.
15. `PUT /api/budgets/{id}` → `source: "user"`; generate again → that limit is unchanged; `POST /{id}/reset` → back to `agent_limit_amount`.
16. `GET /api/budgets/status?month=...` → investments appear only in `invested_this_month`.
17. `POST /api/forecast/generate?horizon_days=60` without a balance → 400 for a CSV without balances; with
    `opening_balance=45000` → 200, `trajectory` has 60 days, `low_confidence` true for a short history.
18. `GET /api/forecast/recurring` → your salary and rent with their next dates; `GET /api/forecast/latest` → the same run.
19. `POST /api/investing/plan` before a profile → 409. `PUT /api/investing/profile` with brave answers (all 5) and
    `liquid_savings` under 3 months of your essentials → `category` not aggressive, `limiting_factor: "capacity"`.
    Raise `liquid_savings` above 6 months and save again → the category rises.
20. `POST /api/investing/plan` after a 30-day forecast → `surplus.source: "forecast"`, `forecast_run_id` set, a
    `disclaimer`, `explanation_source` `llm` or `template`. Without the forecast → `warnings` has `forecast_missing`.
21. `POST /api/investing/simulate` with `years: 41` → 422; with `years: 10` → a range, and `GET /api/investing/plans`
    does not list it.

## Common mistakes
- Running `pytest` on the host instead of in the container (different Python, missing deps).
- Writing a test with a real narration, name or account number. Synthetic only.
- Letting a test call a real LLM. Inject a fake (see `test_expense_categorizer.py`).
- Adding a model column without a migration. The drift test will (correctly) fail.
