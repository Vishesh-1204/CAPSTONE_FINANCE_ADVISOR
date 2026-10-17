# Module: Monthly summary

**Files:** `services/summary_service.py`, `GET /api/transactions/summary` in `api/routes/transactions.py`,
`schemas/transaction.py` (`MonthlySummary`, `CategoryTotal`)

## Why it exists
The first "money picture" of a month, and the input Phase 4 (budgets) will build on. Its key job is to
**not** count investments, redemptions or self-transfers as spending or income.

## How it works
`monthly_summary(db, user_id, month)`:
1. Month bounds in UTC (`YYYY-MM`, default current month; bad format → 422).
2. One grouped SQL query: sum and count per (direction, txn_type, category) for the user and month.
3. Each group goes to the bucket of its `txn_type`. Rows not typed yet (pre-v3, before a categorize run)
   count as expense (debit) or income (credit).
4. `by_category` is built from **expense** rows only, largest first; uncategorized expenses show as "Uncategorized".

| Field | Meaning |
|---|---|
| `expense_total` | `txn_type = expense` only (includes P2P transfers to other people) |
| `income_total` | salary, interest, dividend, refunds, other income |
| `investment_out_total` | SIPs, broker top-ups, FDs (saved, not spent) |
| `redemption_total` | money back from investments (not income) |
| `transfer_total` | between your own accounts |
| `net_investment_flow` | invested − redeemed (cash flow, not a return) |
| `savings_investment_rate` | `(income − expense) / income`; money invested counts as saved; `null` without income |

All arithmetic uses `Decimal`; values are rounded to paise before JSON.

## Common mistakes
- Adding `investment_out_total` to expenses in a client. That is exactly the distortion this design avoids
  (+38% expense total on the evaluation data).
- Expecting this month's numbers with `month` omitted while testing September fixtures: pass `month=2026-09`.

## How to test
- `tests/test_transactions_api_v3.py`: summary excludes investments, redemptions and self-transfers;
  no income → `savings_investment_rate: null`; bad `month` → 422.
- Manual: upload `statement.xlsx`, categorize, `GET /summary?month=<fixture month>`; check
  `expense_total` + `investment_out_total` + `transfer_total` equals total debits.

## Limitations / next
Phase 4 adds budgets per category on top of `by_category`; Phase 5 forecasts these totals.
Returns on investments need broker data (Phase 6).
