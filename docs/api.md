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
```
