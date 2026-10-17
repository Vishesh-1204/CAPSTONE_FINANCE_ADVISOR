# Database

PostgreSQL 16 in Docker (`docker-compose.yml`, service `db`). Tables are defined in
`backend/app/models/` and created/changed **only** by Alembic migrations in `backend/migrations/versions/`.

## Migrations

| Revision | File | What it does |
|---|---|---|
| `0001` | `0001_baseline.py` | The schema as it was at the end of Phase 3B v2 (`users`, `transactions`) |
| `0002` | `0002_statement_ingestion_and_overrides.py` | v3: new `transactions` columns, `amount` Float → `Numeric(14,2)`, new tables `import_batches` and `payee_rules`. Existing rows survive: every new column is nullable or has a server default |

The backend container runs `alembic upgrade head` before starting uvicorn (`backend/Dockerfile`),
so a normal `docker compose up --build` always brings the schema up to date. **You no longer need
`docker compose down -v` after a schema change.**

Adding a column, step by step:

1. Change the model in `app/models/`.
2. Create a revision: `docker compose exec backend alembic revision -m "short description"`, then write
   `upgrade()` and `downgrade()` by hand (or review an `--autogenerate` draft line by line).
3. Restart the backend (or `docker compose exec backend alembic upgrade head`).
4. Run the tests. `tests/test_migrations.py` fails if the models and the migrations disagree ("drift").

Common mistakes: importing app code (for example the type detector) inside a migration. It breaks
when that code changes later, so migrations use plain SQL only. Another: forgetting `downgrade()`.

## Entity relationships

```mermaid
erDiagram
    users ||--o{ transactions : owns
    users ||--o{ import_batches : uploads
    users ||--o{ payee_rules : creates
    import_batches ||--o{ transactions : "produced (nullable)"
```

## `users`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | indexed |
| `email` | varchar(255) | unique, indexed |
| `full_name` | varchar(255) | |
| `hashed_password` | varchar(255) | bcrypt hash, never the password |
| `is_active` | bool | default true |
| `created_at` | timestamptz | server default `now()` |

## `transactions`

One row = one statement line (debit or credit) of one user.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `user_id` | FK users | indexed; every query filters on it |
| `description` | varchar(500) | raw narration as imported |
| `amount` | numeric(14,2) | always **positive**; `direction` gives the sign |
| `direction` | varchar(6) | `debit` (out) / `credit` (in); default `debit` |
| `txn_type` | varchar(20) null | `expense` / `income` / `investment_out` / `investment_in` / `transfer`. NULL on pre-v3 rows until the next categorize run backfills it |
| `investment_subtype` | varchar(20) null | `sip` / `lump_sum` / `wallet_topup` (only for `investment_out`) |
| `channel` | varchar(10) null | `upi` / `neft` / `imps` / `nach` / `card` / `atm` / `cheque` / `other` |
| `payee_key` | varchar(200) null | UPI ID (lowercase) if present, else `description_key` |
| `description_key` | varchar(200) null | normalized narration (indexed); used by memory, grouping and the LLM |
| `balance` | numeric(14,2) null | running balance printed on the statement |
| `balance_ok` | bool null | balance check result; NULL = could not be checked |
| `category` | varchar(100) null | NULL = uncategorized |
| `category_confidence` | float null | 0–1 |
| `category_reasoning` | varchar(1000) null | shown as the "why" in the UI |
| `category_source` | varchar(20) null | `payee_rule` / `memory` / `rule` / `llm` / `user` |
| `agent_category` | varchar(100) null | the agent's **first** suggestion; kept after a user override (override-rate metric) |
| `user_locked` | bool | true once the user set the category; no agent run touches it |
| `needs_review` | bool | confidence below `CATEGORIZER_REVIEW_THRESHOLD` (0.60), or person-guarded |
| `import_batch_id` | FK import_batches null | indexed; NULL for manual entries |
| `transaction_date` | timestamptz | |
| `created_at` | timestamptz | |

Indexes: `user_id`, `description_key`, `import_batch_id`, and the composite `(user_id, payee_key)`
used by "same payee" lookups.

### Status is computed, not stored

`GET /api/transactions?status=...` maps to:

| status | condition |
|---|---|
| `uncategorized` | `category IS NULL` |
| `needs_review` | `needs_review AND NOT user_locked` (sorted least confident first) |
| `categorized` | `category IS NOT NULL AND NOT needs_review AND NOT user_locked` |
| `locked` | `user_locked` |

## `import_batches`

One row per uploaded file (also feeds the parsing metrics).

| Column | Type | Notes |
|---|---|---|
| `id`, `user_id` | | `user_id` indexed |
| `filename` | varchar(255) | |
| `source_type` | varchar(10) | `csv` / `xlsx` / `pdf` |
| `layout` | varchar(30) null | `hdfc_like` / `sbi_like` / `generic` (label for metrics only) |
| `rows_total`, `rows_imported`, `rows_duplicate`, `rows_invalid` | int | |
| `balance_checked` | bool | the file had a balance column we could verify |
| `balance_mismatches` | int | |
| `created_at` | timestamptz | |

## `payee_rules`

Opt-in "remember this payee" rules.

| Column | Type | Notes |
|---|---|---|
| `id`, `user_id` | | `user_id` indexed |
| `match_type` | varchar(20) | `upi_id` (preferred) or `merchant_key` |
| `match_value` | varchar(200) | the UPI ID or normalized merchant key |
| `category` | varchar(100) | |
| `created_at` | timestamptz | |

Constraint: `UNIQUE(user_id, match_type, match_value)`, so remembering the same payee again
**updates** the rule instead of adding a second one.

## Inspecting the data

```powershell
docker compose exec db psql -U finance_user -d finance_db
```
```sql
\dt
SELECT txn_type, category_source, count(*) FROM transactions GROUP BY 1, 2 ORDER BY 1, 2;
SELECT version_num FROM alembic_version;   -- should be 0002
```
