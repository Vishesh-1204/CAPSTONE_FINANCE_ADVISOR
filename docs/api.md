# API reference

Base URL (local): `http://127.0.0.1:8000`. Interactive docs: `http://127.0.0.1:8000/docs` (Swagger).
The Pydantic shapes live in `backend/app/schemas/`; the TypeScript mirror is `frontend/lib/types.ts`.

## Conventions

- **Auth:** every endpoint except `/health`, `/api/auth/register` and `/api/auth/login` needs
  `Authorization: Bearer <access_token>`. A missing/expired/invalid token returns **401**
  `{"detail": "Could not validate credentials."}`. Tokens last `ACCESS_TOKEN_EXPIRE_MINUTES` (60).
- **Ownership:** a resource that belongs to another user returns **404**, exactly like one that does not exist.
- **Errors:** always `{"detail": "readable text"}`, except FastAPI validation errors (422), which are
  `{"detail": [{"loc": ["body", "category"], "msg": "...", ...}]}`.
- **Trailing slashes:** collection routes are declared as `/` under their prefix, so the canonical
  paths are `/api/transactions/`, `/api/payee-rules/`, `/api/categories/`. Without the slash FastAPI
  answers `307` redirect, which some clients do not follow with the `Authorization` header.
- **Money:** stored as `Numeric(14,2)`, sent as JSON numbers, always positive; `direction` gives in/out.
- **CORS:** allowed origins come from `CORS_ORIGINS` (default `http://localhost:3000`).

## Endpoint index

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check (no auth) |
| POST | `/api/auth/register` | Create a user |
| POST | `/api/auth/login` | Get a JWT (OAuth2 password form) |
| POST | `/api/statements/upload` | Import a CSV / XLSX / PDF statement |
| GET | `/api/import-batches` | Your uploads, newest first |
| GET | `/api/import-batches/{id}` | One upload |
| POST | `/api/transactions/` | Add one transaction manually |
| GET | `/api/transactions/` | List with filters + paging |
| GET | `/api/transactions/summary` | Monthly totals |
| POST | `/api/transactions/upload-csv` | **Deprecated** alias of statement upload (CSV only) |
| POST | `/api/transactions/categorize` | Run the agent |
| POST | `/api/transactions/bulk-category` | One category on many rows |
| PATCH | `/api/transactions/{id}/category` | Set/confirm a category (locks the row) |
| POST | `/api/transactions/{id}/unlock` | Remove the lock |
| GET | `/api/payee-rules/` | Your payee rules |
| DELETE | `/api/payee-rules/{id}` | Delete a payee rule |
| GET | `/api/categories/` | Allowed category labels per direction |
| GET | `/api/profile/financial` | Your income and savings target (404 until saved) |
| PUT | `/api/profile/financial` | Create or update income and savings target |
| GET | `/api/profile/financial/suggestion` | Suggested monthly income from your income credits (read-only) |
| POST | `/api/budgets/generate?month=` | Build (or rebuild) the month's budget |
| GET | `/api/budgets/?month=` | The month's limits |
| PUT | `/api/budgets/{id}` | Your own limit for one category |
| POST | `/api/budgets/{id}/reset` | Back to the planner's limit |
| GET | `/api/budgets/status?month=` | Spending vs limits for the month |
| POST | `/api/forecast/generate?horizon_days=` | Forecast the next 30/60/90 days (saved) |
| GET | `/api/forecast/latest` | Your newest saved forecast |
| GET | `/api/forecast/history` | Your saved forecasts (summary), newest first |
| GET | `/api/forecast/recurring` | Detected salary, bills and SIPs (read-only) |
| GET | `/api/forecast/runs/{id}` | One saved forecast |
| PUT | `/api/investing/profile` | Save the risk questionnaire and facts; returns the risk profile |
| GET | `/api/investing/profile` | Your risk profile (404 until saved) |
| PUT | `/api/investing/holdings` | Replace your holdings list (rupees per asset class) |
| GET | `/api/investing/holdings` | Your holdings with weights |
| POST | `/api/investing/plan` | Generate and save an investment plan (409 without a profile) |
| GET | `/api/investing/plan/latest` | Your newest plan |
| GET | `/api/investing/plans` | Your plans (summary), newest first |
| POST | `/api/investing/simulate` | What-if projection (not saved) |
| GET | `/api/investing/assumptions` | The return/volatility assumptions in force |

---

## Auth

### `POST /api/auth/register`
JSON body `{"email": "a@example.com", "full_name": "Test User", "password": "..."}`.
**201** → `UserResponse {id, email, full_name, is_active, created_at}`.
**400** "An account with this email already exists." · **422** invalid email.

### `POST /api/auth/login`
Body is **form-encoded** (OAuth2 password flow), and the email goes in the `username` field:
`username=a@example.com&password=...`. **200** → `{"access_token": "...", "token_type": "bearer"}`.
**401** "Incorrect email or password."

Common mistake: sending JSON to `/login` gives 422. In Swagger use the **Authorize** button instead.

## Statements

### `POST /api/statements/upload`
Multipart form: `file` (required, `.csv`, `.xlsx` or `.pdf`, ≤ 10 MB) and `password` (optional, PDFs only; never stored or logged).

**201** → `StatementImportSummary`:
```json
{
  "import_batch_id": 3, "filename": "sbi_like.pdf", "source_type": "pdf", "layout": "sbi_like",
  "rows_total": 24, "rows_imported": 24, "rows_duplicate": 0, "rows_invalid": 0,
  "by_txn_type": {"expense": 15, "income": 3, "investment_out": 4, "investment_in": 1, "transfer": 1},
  "balance_checked": true, "balance_mismatches": 1, "balance_mismatch_rows": [18],
  "next_step": "POST /api/transactions/categorize"
}
```
(Illustrative values.) Rows are saved **uncategorized**; call `/categorize` next.

| Status | When |
|---|---|
| 400 | Unsupported extension · no header row found · no valid rows · "This PDF is password-protected. Enter its password…" · "The PDF password is incorrect." · not a readable PDF · too many pages (>200) |
| 413 | File larger than 10 MB |
| 422 | "This looks like a scanned PDF. Scanned statements are not supported yet." |

### `GET /api/import-batches` · `GET /api/import-batches/{id}`
→ `ImportBatchResponse[]` (newest first) / one `ImportBatchResponse`
`{id, filename, source_type, layout, rows_total, rows_imported, rows_duplicate, rows_invalid, balance_checked, balance_mismatches, created_at}`. **404** if not yours.

## Transactions

### `POST /api/transactions/`
JSON `{"description": "...", "amount": 450.0, "transaction_date": "2026-09-01T00:00:00Z", "direction": "debit"}`
(`amount > 0`; `direction` defaults to `debit`). **201** → `TransactionResponse`. The row is enriched
(`channel`, `payee_key`, `txn_type`) but not categorized.

### `GET /api/transactions/`
Query parameters (all optional):

| Param | Values |
|---|---|
| `status` | `needs_review` · `uncategorized` · `categorized` · `locked` (see [database.md](database.md#status-is-computed-not-stored)) |
| `txn_type` | `expense` · `income` · `investment_out` · `investment_in` · `transfer` |
| `category` | any label (case-insensitive); unknown label → 422 |
| `date_from`, `date_to` | `YYYY-MM-DD`, both inclusive |
| `limit`, `offset` | default 50 / 0; `limit` max 500 |
| `needs_review` | **deprecated** boolean, use `status=needs_review` |

→ `{"total": 114, "limit": 50, "offset": 0, "items": [TransactionResponse, ...]}`. `total` counts all
matching rows (for paging). Order: newest first; for `status=needs_review`, least confident first.

`TransactionResponse` fields: `id, description, amount, direction, txn_type, investment_subtype, channel,
payee_key, category, category_confidence, category_reasoning, category_source, agent_category,
user_locked, needs_review, balance, balance_ok, import_batch_id, transaction_date, created_at`.

### `GET /api/transactions/summary?month=YYYY-MM`
Default: current UTC month. Bad format → 422.
```json
{
  "month": "2026-09", "transaction_count": 24,
  "expense_total": 18250.0, "income_total": 90000.0,
  "investment_out_total": 15000.0, "redemption_total": 2000.0, "transfer_total": 5000.0,
  "net_investment_flow": 13000.0, "savings_investment_rate": 0.7972,
  "by_category": [{"category": "Housing & Rent", "total": 12000.0, "count": 1}]
}
```
Only `txn_type='expense'` counts as spending; `by_category` lists expenses only (uncategorized
expenses appear as `"Uncategorized"`). `savings_investment_rate = (income − expense) / income`, `null`
without income. Details: [modules/summary.md](modules/summary.md).

### `POST /api/transactions/categorize?reset_ai_results=false`
Runs the agent over every uncategorized, **unlocked** row. `reset_ai_results=true` first clears
every unlocked category so improved logic can redo them; locked rows are always kept.
Synchronous: with many LLM batches it can take minutes (4 s pause between batches on the free tier).
```json
{
  "total_processed": 24, "categorized_by_payee_rules": 0, "categorized_by_memory": 2,
  "categorized_by_rules": 17, "categorized_by_llm": 3, "person_payees_guarded": 2,
  "needs_review": 3, "failed": 0, "message": null
}
```
`failed > 0` means the LLM was unavailable for those rows: they stay uncategorized and `message` says
to run again later. They are **never** labelled "Other".

### `PATCH /api/transactions/{id}/category`
```json
{"category": "Groceries", "apply_to_similar": false, "remember_payee": false}
```
Sets the category, `category_source='user'`, confidence 1.0, `user_locked=true`; keeps `agent_category`.
`apply_to_similar` also updates your **unlocked** rows of the same payee and direction.
`remember_payee` creates/updates a payee rule (UPI ID if present, else merchant key).
→ `{"transaction": TransactionResponse, "similar_updated": 3, "payee_rule": PayeeRuleResponse | null}`.
**404** not yours · **422** unknown label, or a label that does not fit the direction (e.g. `Salary` on a debit; the message lists the allowed labels).

### `POST /api/transactions/bulk-category`
`{"transaction_ids": [1, 2, 3], "category": "Transfers"}` (1–500 ids). Matching rows are set and locked.
→ `{"updated": 2, "updated_ids": [1, 2], "skipped_incompatible_ids": [3], "not_found_ids": []}`.
Rows the category does not fit are skipped, not rejected, so one bad row does not fail the batch.

### `POST /api/transactions/{id}/unlock`
Clears `user_locked`. The category stays; the agent may redo the row on the next
`/categorize?reset_ai_results=true`. → `TransactionResponse`. **404** if not yours.

### `POST /api/transactions/upload-csv` (deprecated)
Same pipeline as `/api/statements/upload` for `.csv` only (≤ 5 MB), v2 response shape
`{imported, skipped_invalid, skipped_duplicates, skipped_credits (always 0), import_batch_id, next_step}`.
Kept for old clients; new code uses `/api/statements/upload`.

## Payee rules

`GET /api/payee-rules/` → `[{id, match_type, match_value, category, created_at}]`, newest first.
`DELETE /api/payee-rules/{id}` → **204**; already-categorized rows keep their category. **404** if not yours.
There is deliberately no POST: rules are created by `PATCH .../category` with `remember_payee=true`.

## Categories

`GET /api/categories/` → `{"debit": [16 expense labels], "credit": ["Salary", "Interest", "Dividend", "Refund & Cashback", "Other Income", "Investment Redemption", "Transfers"]}`.
Read from `core/categories.py`, so the frontend never keeps its own copy of the list.

## Profile

Details and design: [modules/budget-planning.md](modules/budget-planning.md).

### `GET /api/profile/financial` · `PUT /api/profile/financial`
PUT body: `{"monthly_income": 60000, "savings_target_pct": 20}` (`monthly_income` > 0, at most 2 decimals;
`savings_target_pct` 0 ≤ x < 100, default 20). One profile per user; PUT creates it or updates it.
```json
{"monthly_income": 60000.0, "savings_target_pct": 20.0, "savings_target_amount": 12000.0,
 "spendable_income": 48000.0, "updated_at": "2026-10-17T10:00:00Z"}
```
GET → **404** until a profile is saved. Invalid body → 422.

### `GET /api/profile/financial/suggestion?month=YYYY-MM`
Optional `month` (default: current UTC month; bad format → 422). Averages **income credits** (salary,
interest, dividend, other income; refunds, redemptions and self-transfers excluded) over the
`BUDGET_LOOKBACK_MONTHS` full months before `month`, divided by the months that have any transaction.
Read-only: nothing is saved.
```json
{"month": "2026-10", "lookback_months": 3, "window_start": "2026-07", "window_end": "2026-09",
 "months_used": 3,
 "income_by_month": [{"month": "2026-07", "total": 60000.0}, {"month": "2026-08", "total": 0.0},
                     {"month": "2026-09", "total": 60900.0}],
 "suggested_monthly_income": 40300.0, "message": "Average of 3 month(s) of income credits ..."}
```
No income in the window → `"suggested_monthly_income": null` and a message telling you to import a statement.

## Budgets

All take `month` as `YYYY-MM` (required; `2026-13`, `26-09` → **422**). Someone else's budget id → **404**.

### `POST /api/budgets/generate?month=YYYY-MM`
Builds the plan from your expenses in the lookback months before `month` (no history → baseline plan).
Regenerating keeps your own limits (`source="user"`). **400** without a financial profile.
Response: `month, method ("history"|"baseline"), monthly_income, savings_target_amount, spendable_income,
feasible, shortfall, summary, llm_used, explanation_faithful (true|false|null), data_quality
{months_used, uncategorized_count, needs_review_count, transfers_total}, budgets[]`.
Full example: [modules/budget-planning.md](modules/budget-planning.md#output).

### `GET /api/budgets/?month=YYYY-MM`
`[BudgetItem]`, largest limit first. `BudgetItem`: `id, month, category, limit_amount, agent_limit_amount,
source ("agent"|"user"), reasoning`.

### `PUT /api/budgets/{id}` · `POST /api/budgets/{id}/reset`
PUT body `{"limit_amount": 4500}` (≥ 0) → `source="user"`, `agent_limit_amount` unchanged.
Reset → `limit_amount = agent_limit_amount`, `source="agent"`. Both return the `BudgetItem`.

### `GET /api/budgets/status?month=YYYY-MM`
```json
{"month": "2026-10",
 "items": [{"category": "Groceries", "limit": 6000.0, "spent": 6500.0, "remaining": -500.0,
            "percent_used": 108.3, "status": "exceeded", "source": "agent"}],
 "total_limit": 6000.0, "total_spent": 6500.0, "total_remaining": -500.0,
 "invested_this_month": 5000.0, "savings_target_amount": 10000.0}
```
`status`: `ok` (below `BUDGET_WARNING_THRESHOLD` of the limit), `warning`, `exceeded` (above the limit),
`unbudgeted` (spending with no limit; `limit`, `remaining`, `percent_used` are `null`). Only expenses count
as spending; `invested_this_month` is saved money, not spent. `savings_target_amount` is `null` without a profile.

## Forecast

`horizon_days` must be 30, 60 or 90 (anything else → **422**). `as_of` (optional, `YYYY-MM-DD`) forecasts as if
today were that date; default today (UTC); a future date → **422**. Someone else's run id → **404**.
How it works: [modules/forecasting.md](modules/forecasting.md).

### `POST /api/forecast/generate?horizon_days=30&opening_balance=&as_of=`
Builds, explains and saves a forecast for the `horizon_days` after `as_of`.
- `opening_balance` (optional, ≤ 2 decimals): your current balance. Empty → the latest statement `balance` on or
  before `as_of` (`opening_balance_source="statement"`, with `opening_balance_date`); none → **400**.
- Fewer than 1 complete month of expenses before `as_of` → **422**.

Response `ForecastRunResponse`: `id, as_of, horizon_days, created_at, model_name, history_months,
low_confidence, opening_balance, opening_balance_source ("request"|"statement"), opening_balance_date,
income_source ("recurring"|"profile"|"none"), expected_income, expected_fixed_expenses,
expected_variable_expenses {point, lower, upper}, expected_investments, net_cashflow,
projected_end_balance {point, lower, upper}, cash_crunch_date (null = none), cash_crunch_basis ("lower"),
summary, llm_used, explanation_faithful (true|false|null), model_scores [{model_name, mae, folds}],
recurring_items [RecurringItem], budget_warnings [{month, category, projected, limit, over_by}],
trajectory [{date, balance, lower, upper}]` (one point per day). Full example:
[modules/forecasting.md](modules/forecasting.md#output).

### `GET /api/forecast/latest?horizon_days=` · `GET /api/forecast/runs/{id}`
A saved `ForecastRunResponse`. `latest` is the newest run (of that horizon, if given); **404** before the first run.

### `GET /api/forecast/history?limit=20`
`[{id, as_of, horizon_days, model_name, low_confidence, net_cashflow, projected_end_balance,
cash_crunch_date, llm_used, created_at}]`, newest first; `limit` 1–100.

### `GET /api/forecast/recurring?as_of=`
```json
{"as_of": "2026-02-15",
 "items": [{"key": "synth housing society rent", "kind": "fixed_expense", "category": "Housing & Rent",
            "typical_amount": 19600.0, "typical_day": 5, "occurrences": 17, "last_seen": "2026-02-05",
            "active": true, "projected_dates": ["2026-03-05"]}]}
```
`kind`: `income`, `fixed_expense` or `investment`. `active=false`: not seen for 45 days, so not projected.
`projected_dates` covers the next 30 days. Never fails for lack of history (empty list).

## Investing

**Educational planning tool, not financial advice.** Every response carries `disclaimer` (added by code).
The user is always the one in the token; a `user_id` in a body is ignored. `as_of` (optional, `YYYY-MM-DD`)
works like the forecast's: default today (UTC), a future date → **422**.
How it works: [modules/investing.md](modules/investing.md).

### `PUT /api/investing/profile?as_of=` · `GET /api/investing/profile`
```json
{"answers": {"reaction_to_20pct_drop": 4, "investing_experience": 2, "return_vs_safety": 4},
 "liquid_savings": 150000, "existing_monthly_sip": 3000, "income_stability": 4, "dependents": 1,
 "horizon_years": 10}
```
Answers and `income_stability` 1–5, `dependents` 0–20, `horizon_years` 0–50, money ≥ 0 (≤ 2 decimals); else **422**.
Response: the inputs plus `emergency_fund_months` (savings ÷ average monthly essential spending, 0 when unknown)
and `risk_profile {tolerance_score, capacity_score, tolerance_category, capacity_category, category,
limiting_factor ("tolerance"|"capacity"|"both")}`, `updated_at`, `disclaimer`. GET before a save → **404**.

### `PUT /api/investing/holdings?as_of=` · `GET /api/investing/holdings`
Body `{"holdings": [{"asset_class": "equity"|"debt"|"gold"|"cash", "amount": 80000, "label": "", "as_of": null}]}`
REPLACES the list (`[]` clears it); at most 50 rows; a duplicate `(asset_class, label)` (case and spaces
ignored) → **422**. Response `{holdings [{asset_class, amount, label, as_of, weight}], total, weights_by_class, disclaimer}`.

### `POST /api/investing/plan?as_of=`
Optional body `{"goal_amount": 500000, "goal_years": 5}` (`goal_years` 1–40 needs `goal_amount`; else **422**).
No risk profile → **409**. Builds, explains and saves a plan. Response `InvestmentPlanResponse`: `id, created_at,
risk_profile, target_allocation, current_allocation, rebalance [{asset_class, current_weight, target_weight,
amount_change, action ("add"|"reduce"|"hold")}], sip {capacity_total, recommended_total, change_vs_existing,
emergency_topup, limited_by}, projection_current / projection_recommended {monthly_sip, years, p10, p50, p90,
probability_of_goal, months_to_goal, n_paths}, portfolio {expected_return, volatility}, emergency_fund
{essential_monthly_spend, essentials_source, months_covered, target_months, gap}, surplus {monthly_low, source
("forecast"|"budget"|"none"), forecast_as_of}, goal {amount, years, required_sip}, instruments, assumptions,
warnings, forecast_run_id, explanation, explanation_source ("llm"|"template"), llm_used, explanation_faithful,
claims_compliant, engine_version, disclaimer`.
Warnings: `forecast_missing`, `forecast_stale`, `surplus_unknown`, `essentials_unknown`, `no_holdings`.

### `GET /api/investing/plan/latest` · `GET /api/investing/plans?limit=20`
`latest`: the newest `InvestmentPlanResponse`; **404** before the first. `plans`: `{plans [{id, created_at,
category, recommended_sip, explanation_source}], disclaimer}`, newest first; `limit` 1–100.

### `POST /api/investing/simulate`
Body `{"monthly_sip": 10000, "years": 10, "category": "moderate", "goal_amount": null, "annual_step_up": 0}`:
`years` 1–40, `monthly_sip` ≤ ₹1,00,00,000, `annual_step_up` 0–0.5, `category` conservative|moderate|aggressive.
Not saved; the number of paths and the seed come from settings (a client cannot choose them).
Response `{category, target_allocation, expected_return, volatility, projection, disclaimer}`.

### `GET /api/investing/assumptions`
`{assets [{asset_class, expected_return, volatility}], sip_safety_factor, mc_paths, mc_seed, note, disclaimer}`.
The defaults are illustrative placeholders (see `INVEST_*` in `backend/.env.example`).

---

## Examples in PowerShell

PowerShell's `curl` is an alias for `Invoke-WebRequest`; use `curl.exe` (or Swagger).

```powershell
# login (form-encoded) and keep the token
$token = (curl.exe -s -X POST http://127.0.0.1:8000/api/auth/login `
  -d "username=a@example.com&password=YourTestPassword" | ConvertFrom-Json).access_token

# upload a synthetic statement
curl.exe -s -X POST http://127.0.0.1:8000/api/statements/upload `
  -H "Authorization: Bearer $token" `
  -F "file=@backend/tests/fixtures/statements/protected.pdf" -F "password=test1234"

# categorize, then list the review queue
curl.exe -s -X POST http://127.0.0.1:8000/api/transactions/categorize -H "Authorization: Bearer $token"
curl.exe -s "http://127.0.0.1:8000/api/transactions/?status=needs_review&limit=20" -H "Authorization: Bearer $token"

# budget: suggestion, profile, generate, status
curl.exe -s "http://127.0.0.1:8000/api/profile/financial/suggestion?month=2026-10" -H "Authorization: Bearer $token"
curl.exe -s -X PUT http://127.0.0.1:8000/api/profile/financial -H "Authorization: Bearer $token" `
  -H "Content-Type: application/json" -d '{\"monthly_income\": 60000, \"savings_target_pct\": 20}'
curl.exe -s -X POST "http://127.0.0.1:8000/api/budgets/generate?month=2026-10" -H "Authorization: Bearer $token"
curl.exe -s "http://127.0.0.1:8000/api/budgets/status?month=2026-10" -H "Authorization: Bearer $token"

# forecast: 60 days with a typed balance, then the detected recurring items
curl.exe -s -X POST "http://127.0.0.1:8000/api/forecast/generate?horizon_days=60&opening_balance=45000" -H "Authorization: Bearer $token"
curl.exe -s "http://127.0.0.1:8000/api/forecast/recurring" -H "Authorization: Bearer $token"

# investing: profile, then a plan with a goal (generate a 30-day forecast first for the best surplus)
curl.exe -s -X PUT http://127.0.0.1:8000/api/investing/profile -H "Authorization: Bearer $token" `
  -H "Content-Type: application/json" `
  -d '{\"answers\": {\"reaction_to_20pct_drop\": 4, \"investing_experience\": 2, \"return_vs_safety\": 4}, \"liquid_savings\": 150000, \"income_stability\": 4, \"dependents\": 1, \"horizon_years\": 10}'
curl.exe -s -X POST http://127.0.0.1:8000/api/investing/plan -H "Authorization: Bearer $token" `
  -H "Content-Type: application/json" -d '{\"goal_amount\": 500000, \"goal_years\": 5}'
```
