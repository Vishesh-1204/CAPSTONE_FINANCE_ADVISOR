# Phase 3B v3 Spec: Statement Ingestion, Investment Flows, Manual Override, Test Console

Repo path (commit it here): `docs/phase3b-v3-spec.md`
Source design: "Phase 3B Improvement Design: PDF Ingestion, Investment Flows, Manual Override" (panel feedback).
Applies on top of the verified Phase 3B v2 (hybrid Memory -> Rules -> LLM categorizer, 63 tests passing).

## 0. Ground rules for Claude Code (read first)

1. Read CLAUDE.md and this file. Then INSPECT the real code (do not trust this file's gap list blindly) and report differences before editing.
2. Work in Plan mode first. Work one step (section 11) at a time. After each step: run `docker compose exec backend python -m pytest tests -q`, all tests green, then one git commit. Stop and report at every "GATE".
3. Branch: `feature/vishesh-3b-v3` (create from the current branch after v2 is committed). Never commit `.env`, real bank statements, or sample files with real data. Synthetic data only.
4. Dependencies: only the additions approved in section 2. Anything else needs my approval first, with a one-line reason.
5. Teach as you go: for each step, a short beginner-friendly explanation (why, where it fits, common mistakes, how to test). Keep code production-quality: type hints, logging, no hardcoded values, comments on classes/functions/complex logic, file path + purpose at top of every new file.
6. Layering stays as in CLAUDE.md: routes (HTTP only) -> services (logic) -> agents/llm (reasoning). `user_id` always from the JWT. Every query filters by `user_id`.
7. Privacy rule (non-negotiable): parsing is 100% local. The LLM never receives raw PDFs, account numbers, phone numbers, UPI IDs, reference numbers, balances, or person-name payees. Only cleaned merchant strings.

## 1. Gap analysis (verify against the code)

Already in v2: text normalizer; rule-based keywords; memory from user corrections keyed on `description_key`; batched LLM (20 unique descriptions/request); `category_source` (rule/memory/llm/user); `needs_review` (threshold 0.60); CSV import with header aliases, dd/mm/yyyy, Debit/Credit columns, duplicate protection; PATCH category with `similar_updated`; evaluation harness (rules vs llm vs hybrid).

Missing (this spec):
- Alembic migrations (still `create_all`).
- Credits are skipped on import (`skipped_credits`). Need `direction` and `txn_type`.
- Investment flows (SIP, broker top-ups, redemptions, interest, dividends).
- PDF and XLSX ingestion, password PDFs, scanned-PDF error, balance check.
- Payee-keyed per-user rules table (UPI ID), opt-in "remember this payee".
- `user_locked` (agent never overwrites), `agent_category` (original suggestion stored for feedback metrics).
- Review queue filter and bulk categorize.
- Person-name payees must never reach the LLM.
- Provider fallback chain; optional background job.
- Synthetic bank-style PDFs/CSVs and the extended metrics.
- A usable frontend test console.

## 2. Approved additions (outside the original stack, justified)

| Package | Where | Why it is needed |
|---|---|---|
| `pdfplumber` | runtime | Extract tables and text from digital bank PDFs locally; supports passwords. Not in the approved list: no approved library does PDF table extraction. |
| `openpyxl` | runtime | Read .xlsx statements. |
| `reportlab` | dev only (`requirements-dev.txt`) | Generate synthetic HDFC/SBI-style PDFs, including password-protected ones, for tests. Never shipped in the runtime image. |
| `alembic` | runtime | Migrations (already planned for Phase 4). |
| Celery + Redis | ONLY step S9, optional | Background import/categorize job. In the approved stack but heavy for the dev laptop; do last, ask me first. |

Frontend uses only the approved stack: Next.js, React, TypeScript, Tailwind, Shadcn UI, Recharts, plain `fetch`. No extra state/data libraries.

## 3. Data model (Alembic revision 0001 = baseline of current tables, 0002 = this change)

`transactions` new columns (all nullable or defaulted so existing rows survive):
- `direction`: 'debit' | 'credit'
- `txn_type`: 'expense' | 'income' | 'investment_out' | 'investment_in' (redemption) | 'transfer'
- `investment_subtype`: 'sip' | 'lump_sum' | 'wallet_topup' | NULL
- `payee_key`: normalized merchant key or UPI ID (lowercased), indexed with `user_id`
- `channel`: 'upi' | 'neft' | 'imps' | 'nach' | 'card' | 'atm' | 'cheque' | 'other'
- `balance`: Numeric(14,2) NULL; `balance_ok`: bool NULL
- `agent_category`: the agent's ORIGINAL suggestion, never changed after the agent writes it
- `user_locked`: bool default false (true whenever the user sets a category)
- `import_batch_id`: FK to `import_batches` NULL
- Convert `amount` from Float to Numeric(14,2) in this migration (money must not be float).
- Decision: do NOT add a separate `status` column. `status` is a computed query filter (see API): needs_review / uncategorized / categorized / locked. Keep `category_source` instead of the design doc's `categorized_by` (same meaning, avoids a rename).

New table `import_batches`: id, user_id, filename, source_type (csv/xlsx/pdf), layout (e.g. hdfc_like, sbi_like, generic), rows_total, rows_imported, rows_duplicate, rows_invalid, balance_checked, balance_mismatches, created_at.

New table `payee_rules`: id, user_id, match_type ('upi_id' | 'merchant_key'), match_value, category, created_at; UNIQUE(user_id, match_type, match_value).

Categories: keep the 15 expense categories; ADD expense category `Transfers` (person-to-person payments; defaults to needs_review). ADD income-side labels: `Salary`, `Interest`, `Dividend`, `Refund & Cashback`, `Other Income`, and `Investment Redemption` (NOT income). Keep all labels in `app/core/categories.py`.

## 4. Categorization priority order (single place: categorization_service)

1. `user_locked` rows are never touched by any agent run.
2. Payee rules (per user, UPI ID first, then merchant key).
3. Memory (`description_key` corrections).
4. Merchant dictionary and keyword rules (includes investment platforms and income keywords).
5. LLM, only for unknown MERCHANT-like strings, batched, cleaned strings only.
6. Fallback chain: primary provider -> secondary provider -> leave uncategorized with a clear reason (never label "Other" on failure).

Person-payee guard: a payee that looks like a person (no merchant keyword, personal-name pattern, UPI handle style) is categorized as `Transfers` with `needs_review=true` and is NEVER sent to the LLM. Log the count, not the strings.

## 5. Statement ingestion (strategy pattern)

- `StatementParser` interface -> `CsvParser`, `XlsxParser`, `PdfParser`; all output `ParsedRow(date, narration, ref, debit, credit, balance)`. After that ONE shared pipeline runs (clean narration, detect type, dedupe, save, categorize). No duplicated logic per format.
- PDF: pdfplumber table extraction; detect the header row and map columns; drop repeated page headers/footers; join multi-line narrations into one row (a line without a date continues the previous row); support 2 synthetic layouts (HDFC-like, SBI-like) plus a generic fallback.
- Scanned PDF (no extractable text): return HTTP 422 with the message "This looks like a scanned PDF. Scanned statements are not supported yet."
- Password: accepted as a form field, used only in memory for that request, never stored, never logged, never in error messages. Wrong password -> 400 with a clear message.
- Balance check: for each row verify previous balance +/- amount == balance (tolerance 0.01). Store `balance_ok`; report mismatch count and the first 10 mismatching row numbers in the import summary. This is also a measurable parsing-quality metric for the paper.
- Duplicate protection continues to work across formats.

## 6. Investment flows

- Outflow (debit) to Groww, Zerodha, Angel One, Upstox, INDmoney, and mutual-fund SIP/NACH mandates: `txn_type=investment_out`, category `Investments & Savings`, subtype sip / lump_sum / wallet_topup. EXCLUDED from expense totals; counted in the savings/investment rate.
- Inflow (credit): savings/FD interest -> `income`, `Interest`; dividend -> `income`, `Dividend`; salary -> `income`, `Salary`.
- Broker/MF money coming back (withdrawal, redemption, SIP redeem): `txn_type=investment_in`, category `Investment Redemption`. NOT income (principal + gain mix that cannot be split from the statement).
- Known limitation to state in docs: real returns need broker holdings/statements; net platform flow = sent minus received only. Note for Phase 6 (Investment Advisor).

## 7. Manual override and review queue

- `PATCH /api/transactions/{id}/category` body: `{category, apply_to_similar=false, remember_payee=false}`.
  - Always: set category, `category_source='user'`, confidence 1.0, `user_locked=true`, keep `agent_category` as the original suggestion.
  - `apply_to_similar`: also update the user's existing rows with the same `payee_key` that are not locked.
  - `remember_payee` (opt-in): upsert a `payee_rules` row keyed on UPI ID if present, otherwise merchant key. Never key on a name alone when a UPI ID exists.
- `POST /api/transactions/{id}/unlock`: clears the lock so the agent may re-run.
- `GET /api/transactions?status=needs_review` etc. (see section 8). `POST /api/transactions/bulk-category` for bulk fixes.
- Feedback metric: share of rows where the final category differs from `agent_category` (override rate), overall and per category.

## 8. API list (all JWT-protected; add Pydantic schemas; keep old routes working)

- `POST /api/statements/upload` (multipart: `file`, optional `password`) -> import summary incl. batch id, balance-check result, per-type counts. `POST /api/transactions/upload-csv` stays as a thin backward-compatible alias.
- `GET /api/import-batches`, `GET /api/import-batches/{id}`
- `GET /api/transactions` filters: `status` (needs_review | uncategorized | categorized | locked), `txn_type`, `category`, `date_from`, `date_to`, `limit`, `offset`; response includes total count.
- `POST /api/transactions/categorize` (respects locks), `PATCH .../category`, `POST .../unlock`, `POST /api/transactions/bulk-category`
- `GET /api/payee-rules`, `DELETE /api/payee-rules/{id}`
- `GET /api/transactions/summary?month=YYYY-MM` -> `expense_total` (expense type only), `income_total`, `investment_out_total`, `redemption_total`, `savings_investment_rate`, `by_category` (expenses only).
- Add CORS middleware reading `CORS_ORIGINS` from env (default `http://localhost:3000`).

## 9. Tests and metrics

- Unit tests for: parsers (CSV/XLSX/PDF incl. multi-line narration, header/footer removal, scanned PDF, wrong/right password), balance check, type detection (every investment and income case), person-payee guard (assert no person string reaches the fake LLM), payee rules priority, lock behaviour (agent never overwrites), summary excluding investments, bulk, review queue filters, migrations apply cleanly on an empty DB.
- Synthetic fixtures generated by `scripts/make_synthetic_statements.py` (reportlab, dev only): HDFC-like PDF, SBI-like PDF, password-protected PDF, scanned-style PDF (image only), XLSX, messy CSV. All made-up names/numbers.
- Extend `evaluation/`: rule hit-rate, memory hit-rate, LLM calls per 100 rows, accuracy, macro-F1, per-category precision/recall, latency, parse success rate per layout, balance-check pass rate, override rate. Add credit/investment rows to the labeled set. Write results to a CSV for the paper.
- Ablations to support: rules off, memory off, payee rules off, person-guard off (privacy leakage count), investment-exclusion off (budget distortion).

## 10. Frontend "Test Console" (bounded; NOT the Phase 12 dashboard)

Location: `frontend/` (Next.js + TypeScript + Tailwind + Shadcn UI + Recharts). Run with `npm run dev` on the host (not in Docker, to save RAM). `NEXT_PUBLIC_API_URL=http://127.0.0.1:8000` in `frontend/.env.local` (gitignored; provide `.env.local.example`).
- Inspect the existing `frontend/` folder first and build on it if it is a real Next.js app.
- Pages: `/login` (register + login, token kept in localStorage for the test console only; note httpOnly cookies come in Phase 12), `/upload` (CSV/XLSX/PDF drag-drop, password field, import summary, balance-check result), `/transactions` (table with filters, source badges rule/memory/llm/user, confidence bar, reasoning tooltip, lock icon, inline category edit with the two checkboxes "apply to similar" and "remember this payee"), `/review` (queue with bulk select + bulk category), `/summary` (Recharts: expense by category, income vs expense vs investment), `/rules` (list/delete payee rules).
- Every page has a collapsible "Raw API response" panel and shows API errors readably (this is the testing aid). A "Run categorizer" button calls `POST /categorize` and shows the summary.
- A small shared `lib/api.ts` client (typed, attaches the token, handles 401 by redirecting to login). Components small and reusable so Phase 12 can extend them. No pages for budgets/forecast.

## 11. Steps (stop at each GATE)

- S0: Inspect code, report gaps and any file that differs from this spec. Create the branch. GATE.
- S1: Alembic setup + baseline revision 0001 (existing schema) + docker/entrypoint change (`alembic upgrade head` before uvicorn). Verify on a fresh DB and on the current DB (stamp). GATE.
- S2: Revision 0002 (new columns, tables, Numeric amount) + models/schemas + `txn_type` detection + merchant/investment dictionary + extended categories. Tests. GATE.
- S3: Payee rules, `user_locked`, `agent_category`, extended PATCH, unlock, bulk, review-queue filters, summary endpoint, person-payee guard, provider fallback. Tests. GATE.
- S4: Parser interface + CSV refactor onto it + import batches + `POST /api/statements/upload` + credits imported. Tests. GATE.
- S5: XLSX + PDF parsers, password, scanned error, balance check, synthetic fixture generator. Tests. GATE.
- S6: Extend evaluation harness + labeled data; produce the metrics CSV. GATE.
- S7: Frontend console (can be a separate session). GATE.
- S8: Update CLAUDE.md "Current status", add `docs/` notes (architecture + flow diagram + API + tables + testing per module). Final full test run. Open PR into DEV.
- S9 (OPTIONAL, ask first): Celery + Redis background import/categorize with a batch-status endpoint.
