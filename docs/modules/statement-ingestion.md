# Module: Statement ingestion

**Files:** `services/statement_parsers/` (`base.py`, `csv_parser.py`, `xlsx_parser.py`, `pdf_parser.py`,
`tabular.py`, `__init__.py`), `services/statement_import_service.py`, `api/routes/statements.py`,
`models/import_batch.py`, `schemas/statement.py`

## Why it exists
Users have bank statements as CSV, Excel or PDF, in different layouts. The rest of the system needs
one clean list of rows: date, narration, debit or credit, balance. This module turns any supported
file into that list, locally, and saves only the rows that are new.

## Where it fits
`POST /api/statements/upload` → **this module** → rows saved uncategorized → [categorization](categorization.md).
`transaction_enrichment.enrich_transaction` ([transaction types](transaction-types.md)) runs on every saved row.

## How it works

### 1. Strategy pattern: one interface, three parsers
`StatementParser.parse(file_bytes, password) -> ParseResult` (`base.py`). `get_parser(filename)` picks
the class by extension (`.csv`, `.xlsx`, `.pdf`); anything else → 400. Parsers are pure: no database,
no HTTP, no LLM. A `ParsedRow` has `row_number, date, narration, debit, credit, balance, ref` with exactly
one of debit/credit > 0.

Each parser only turns the file into a **grid of cells**; `tabular.assemble_rows` does the rest:

| Parser | Format-specific work |
|---|---|
| `CsvParser` | decode bytes, guess the delimiter |
| `XlsxParser` | read the first sheet with openpyxl (real date/number cell values) |
| `PdfParser` | open with password; detect scanned PDFs; ruled tables via pdfplumber's table extractor (HDFC-like); if no header is found, fall back to word positions: the header words' x-positions become column bands (SBI-like, unruled) |

### 2. Shared logic (`tabular.py`)
- **Header detection:** scans the first 30 rows for a row that maps (via `HEADER_ALIASES`) to a
  description, a date and at least one money column. Banks put account details above the table, so the header is often not row 1.
- **Amounts:** handles `Rs.`, `INR`, `₹`, commas, `Dr`/`Cr` suffixes, a single signed amount column or
  separate withdrawal/deposit columns, or a `Dr/Cr` indicator column.
- **Dates:** 12 formats (`DATE_FORMATS`), day-first Indian style.
- **Furniture:** repeated page headers, "Page 1 of 2", totals and footers are skipped; an
  "Opening Balance" line is captured for the balance check.
- **Multi-line narrations:** a line without a date continues the previous row's narration.
- **Layout label** (`hdfc_like` / `sbi_like` / `generic`): for metrics only; mapping always uses aliases, so other banks work too.
- Unreadable transaction-like rows are counted as `invalid`, not fatal.

### 3. The pipeline (`statement_import_service.import_statement`)
1. Parse (parser errors → HTTP errors with safe messages).
2. **Balance check** (`check_balances`): `previous − debit + credit == balance` within ₹0.01. Handles
   newest-first statements; after a mismatch the chain restarts, so one bad row gives one mismatch, not a cascade.
   The first row is checkable only when there is an opening balance line.
3. **Dedupe:** key = (narration without whitespace, lowercased; exact amount; day; direction), compared
   with your existing rows in the file's date range, so it works **across formats** (a PDF and a CSV of the same statement).
4. Insert an `ImportBatch`, then the new `Transaction`s (debits **and** credits) with `balance`,
   `balance_ok`, `import_batch_id`, each enriched locally.
5. Return the summary (`by_txn_type`, balance-check result, first 10 mismatching row numbers).

### Password and scanned PDFs
- The password lives only inside the `parse` call: never stored, logged or echoed in an error.
  No password on a protected PDF → 400 "This PDF is password-protected…"; wrong one → 400 "The PDF password is incorrect."
- No extractable text on any page → **422** "This looks like a scanned PDF…". There is no OCR.

## Design decisions
- Parsing is separate from categorizing: uploads are fast, and an LLM outage never loses imported rows.
- `Decimal` everywhere for money (`core/money.py`).
- Balance check doubles as a parsing-quality metric for the paper (balance pass rate).

## Common mistakes
- Adding format-specific logic to the import service. Put it in the parser; the pipeline must stay shared.
- Setting `Content-Type` manually for multipart uploads in a client (breaks the boundary).
- Testing with real statements. Use the synthetic fixtures (privacy rule).
- Two genuinely identical payments on the same day with the same narration within ONE file are both
  kept (dedupe compares against stored rows only), but re-uploading that file later skips both.

## How to test
- Automated: `tests/test_statement_parsers.py` (27: XLSX, both PDF layouts, password right/wrong/missing,
  scanned 422, balance check, cross-format duplicates), `tests/test_statement_import.py` (22: CSV through the
  pipeline: headers, amounts, dates, credits, duplicates, batches), `tests/test_statements_api.py` (6: HTTP).
- Fixtures: `backend/tests/fixtures/statements/` (`hdfc_like.pdf`, `sbi_like.pdf` with one planted balance
  error, `protected.pdf` password `test1234`, `scanned.pdf`, `statement.xlsx`, `messy.csv`, `ground_truth.json`).
  Regenerate with `docker compose exec backend python -m scripts.make_synthetic_statements`.
- Manual: upload each fixture in Swagger; upload one twice (second time all duplicates); upload
  `sbi_like.pdf` and expect `balance_mismatches: 1`.

## Known limitations
- Scanned PDFs (no OCR); 200-page and 10 MB limits; first worksheet only for XLSX.
- Offline parsing metrics (latest run): row recall 1.0 on HDFC-like/XLSX/CSV, 0.958 on SBI-like;
  narration fidelity 0.958 (HDFC-like) and 0.875 (SBI-like) because PDF line wraps can split words.

## Connects to
[transaction-types.md](transaction-types.md) (enrichment) → [categorization.md](categorization.md) → [evaluation.md](evaluation.md) (parsing metrics).
