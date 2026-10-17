# Module: Transaction types & investment flows

**Files:** `services/narration_parser.py`, `services/text_normalizer.py`, `services/txn_type_detector.py`,
`services/transaction_enrichment.py`, `core/merchant_dictionary.py`, `core/transaction_types.py`,
`core/categories.py`, `core/money.py`

## Why it exists
Before asking "which category?", the system must know **what kind** of money movement a row is.
A ₹5,000 SIP is not spending; a Groww withdrawal is not income. If investments were counted as
expenses, the budget agent (Phase 4) would tell a disciplined investor they overspend. On the
evaluation data, counting investments as spending would inflate the expense total by **38%**.

## How it works

### 1. Narration parsing (`narration_parser.parse_narration`)
From a raw narration like `UPI/412345678901/SWIGGY/swiggy@icici` it extracts, locally:
- `upi_id` (`swiggy@icici`): regex for `name@handle`; skips e-mail addresses.
- `channel`: `upi`, `neft`, `imps`, `nach`, `card`, `atm`, `cheque`, or `other`, from rail words.
- `description_key`: via `text_normalizer.normalize_description` (below).
- `payee_key`: the UPI ID if present, else the `description_key`.

### 2. Normalization (`text_normalizer.normalize_description`)
Lowercase; remove UPI handles, punctuation, pure numbers, long alphanumeric reference codes and rail
words (UPI, NEFT…). `UPI/412345678901/SWIGGY/okaxis` → `swiggy`. This key is what memory, grouping and
the LLM use. **Digits are stripped**, so "Shop 1" and "Shop 2" become the same key (a known, accepted trade-off).

### 3. Type detection (`txn_type_detector.detect_txn_type`)
Keyword data lives in `core/merchant_dictionary.py` (brokers, mutual-fund words, SIP/mandate words,
salary/interest/dividend/refund words, self-transfer words). Matching is on the normalized text, on word boundaries.

| Direction | Checks, in order | Result |
|---|---|---|
| debit | self-transfer words | `transfer`, Transfers |
| debit | broker / MF / savings product | `investment_out`, Investments & Savings; subtype `sip` (SIP/mandate word or NACH), `wallet_topup` (broker, not MF), else `lump_sum` |
| debit | none | `expense` (category decided later) |
| credit | self-transfer → refund → dividend → interest → salary → redemption words → investment platform | `transfer` / `income` (Refund & Cashback, Dividend, Interest, Salary) / `investment_in` (Investment Redemption) |
| credit | none | `income`, Other Income, confidence 0.5 (goes to review) |

The order for credits matters: a refund **from** a broker is a refund, and a dividend paid via a broker is a dividend.
**Every credit gets a deterministic label**, so credits never reach the LLM.

### 4. Enrichment (`transaction_enrichment.enrich_transaction`)
The one function every ingestion path calls before saving (statement import, manual entry), and the
categorizer calls for old rows (`_backfill_types`). It fills `description_key, payee_key, channel, txn_type, investment_subtype`.
`sync_txn_type_with_category` keeps `txn_type` consistent when a category is chosen later
(e.g. the user picks Investments & Savings → `investment_out`).

### 5. Categories (`core/categories.py`)
The single source of truth: 16 expense labels (with definitions used in the LLM prompt) and the
credit-side labels (Salary, Interest, Dividend, Refund & Cashback, Other Income, Investment Redemption;
Transfers fits both). `category_fits_direction` stops nonsense like Salary on a debit.

## Common mistakes
- Adding a broker name in code instead of `merchant_dictionary.py` (data and logic are kept apart).
- Adding a short keyword that matches inside other words (e.g. `amc` = "annual maintenance contract" too; it is deliberately absent).
- Treating redemptions as income. They mix principal and gain, which a bank statement cannot split.

## How to test
- `tests/test_txn_type_detector.py` (36): every investment and income case from spec section 6, plus `to_money`.
- `tests/test_narration_parser.py` (16): channels, UPI IDs, payee keys. `tests/test_text_normalizer.py` (9).
- Manual: upload `statement.xlsx`; the summary's `by_txn_type` shows investment and income rows before any categorization.

## Known limitations
- Keyword-based: a new broker or unusual narration falls back to `expense`/`Other Income` until added.
- Net investment flow (sent − received) is cash flow, not returns; real returns need holdings data (Phase 6).

## Connects to
[statement-ingestion.md](statement-ingestion.md) (calls enrichment) → [categorization.md](categorization.md) (layer 3a) → [summary.md](summary.md) (totals by type).
