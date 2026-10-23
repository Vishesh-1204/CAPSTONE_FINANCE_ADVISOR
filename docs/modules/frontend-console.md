# Module: Frontend Test Console (S7)

**Folder:** `frontend/` (Next.js 16, React 19, TypeScript, Tailwind 4, Shadcn UI, Recharts, plain `fetch`)

## Why it exists
A small browser tool to exercise the Phase 3B API (upload, categorize, review, override, summary,
payee rules) without hand-crafting Swagger requests. It is a **testing aid, not the Phase 12 dashboard**,
but its client and components are written to be reused there.

## Status

| Step | Content | State |
|---|---|---|
| F0 | Scaffold, `lib/api.ts`, shared components, home page = backend connection check, `GET /api/categories` (backend) | **done** |
| F1 | `/login` (login + register) | **done** |
| F2 | `/upload` (drag-drop, password, import summary, run categorizer, past batches) | **done** |
| F3 | `/transactions` (filters, paging, source badges, confidence bar, reasoning tooltip, lock, inline edit with "apply to similar" / "remember this payee") | **done** |
| F4 | `/review` (needs-review queue, bulk select + bulk category) | **done** |
| F5 | `/summary` (Recharts: expense by category; income vs expense vs investment) | **done** |
| F6 | `/rules` (list / delete payee rules) | **done** |
| P8 | `/budgets` (Phase 4: financial profile + income suggestion, generate plan, limits table with inline override / reset, status bars, Recharts spent vs limit) | **done** |
| F10 | `/forecast` (Phase 5: 30/60/90 toggle, opening balance prefilled from the latest statement balance, cards, Recharts balance line with 80% band, cash-crunch banner, low-confidence notice, template / AI badge, budget warnings, recurring payments) | **done** |
| F11 | `/investing` (Phase 6C: risk profile with the limiting factor in plain words, holdings editor with weights, generate plan with an optional goal, target vs current allocation chart + rebalance table, SIP card, bad / typical / good projection with goal probability, instrument types, assumptions panel, warnings, AI-written / Template badge, plan history (opens any saved plan), what-if simulator, permanent disclaimer banner) | **done** |

## Run it (on the host, not in Docker)
```powershell
docker compose up --build                 # backend + DB, terminal 1
cd frontend
copy .env.local.example .env.local        # NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
npm install
npm run dev                               # http://localhost:3000
```
Open **http://localhost:3000**, not `127.0.0.1:3000`: CORS compares origins exactly and only `localhost:3000` is allowed by default.
Restart `npm run dev` after changing `.env.local` (`NEXT_PUBLIC_` values are baked in at build time).

## Structure (what exists)

| Path | Role |
|---|---|
| `lib/api.ts` | The only code that calls the backend: base URL, Bearer token, readable `ApiError` (flattens both FastAPI error shapes), 401 → clear token, records every call for the raw panel |
| `lib/types.ts` | TypeScript mirror of `backend/app/schemas/` |
| `lib/auth.ts` | Token in `localStorage` (**test console only**; Phase 12 uses httpOnly cookies) |
| `lib/format.ts` | ₹ formatting (en-IN), dates, percentages |
| `hooks/use-api-data.ts` | Load-on-open / reload pattern (no stale responses, lint-clean in React 19) |
| `hooks/use-api-log.ts`, `components/raw-response-panel.tsx` | The collapsible "Raw API response" panel on every page |
| `components/api-error-alert.tsx` | Readable red error box |
| `components/auth-guard.tsx`, `app/(console)/layout.tsx` | Pages inside the `(console)` route group require a token |
| `components/ui/` | Shadcn-generated components |
| `app/(console)/transactions/page.tsx` | `/transactions`: owns the filters, the page offset and the loaded page; one changed row is swapped locally, a change that may touch other rows (apply to similar, new payee rule) refetches |
| `components/transaction-table.tsx` | Shared by /transactions and /review: badges, confidence bar, reasoning tooltip, lock/unlock, inline editor (category + the two checkboxes), optional row selection |
| `components/transaction-badges.tsx` | `SourceBadge` (rule / memory / llm / user / payee rule), `TxnTypeBadge`, `ConfidenceBar` |
| `components/transaction-filters.tsx`, `components/pager.tsx` | Filter bar (one control per query parameter) and Previous / Next with "Showing x–y of total" |
| `components/category-select.tsx` | Category dropdown fed by `GET /api/categories/` (never hard-coded); `ANY` sentinel for "no filter" |
| `app/(console)/review/page.tsx` | `/review`: the needs-review queue (least confident first), selection, bulk category with skipped / not-found ids reported |
| `app/(console)/summary/page.tsx`, `components/summary-charts.tsx` | `/summary`: totals by transaction type, savings + investment rate, expenses by category (uncategorized in amber), income vs expenses vs investments |
| `app/(console)/rules/page.tsx` | `/rules`: payee rules with a two-step delete (Delete → Confirm delete) |
| `components/month-picker.tsx` | The `YYYY-MM` month input shared by /budgets and /summary (half-typed values are ignored) |
| `app/(console)/budgets/page.tsx` | `/budgets`: owns the month and three loads (profile, limits, status); refetches after every change |
| `components/financial-profile-card.tsx` | Income + savings % form (`PUT /api/profile/financial`), "Suggest from my income" (fills the field, saves nothing) |
| `components/budget-plan-card.tsx` | Result of `POST /api/budgets/generate`: method, summary, feasibility, LLM used / guard result, data quality. Only shown right after generating (the summary is not stored) |
| `components/budget-table.tsx` | Limits with source badge and reasoning tooltip; inline edit (`PUT /api/budgets/{id}`) and reset (`POST .../reset`) |
| `components/budget-status-card.tsx` | `GET /api/budgets/status`: totals, progress bars by status, unbudgeted spending, Recharts spent vs limit |
| `app/(console)/forecast/page.tsx` | `/forecast`: owns the horizon, the opening-balance input and three loads (latest run of the horizon, recurring items, recent rows for the balance prefill) |
| `components/forecast-result.tsx` | One `ForecastRun`: model badge (hover = backtest error per model), template / AI badge, crunch banner, low-confidence notice, cards, explanation, budget warnings |
| `components/forecast-balance-chart.tsx` | Recharts `ComposedChart`: range `Area` for the 80% band (`[lower, upper]` per day), `Line` for the balance, ₹0 and crunch-date reference lines |
| `components/recurring-items-table.tsx` | `GET /api/forecast/recurring`: kind, payee key, category, typical amount, usual day, last seen, next dates; stopped items greyed |
| `app/(console)/investing/page.tsx` | `/investing`: owns five loads (profile, holdings, latest plan, plan history, assumptions), the goal inputs and which plan is shown (opened from the history or just generated, else the latest); permanent disclaimer banner |
| `lib/investing.ts` | Plain words for the investing codes: asset classes, limiting factor, rebalance actions, SIP limits, warning codes |
| `components/risk-profile-card.tsx` | Questionnaire (tolerance) + facts (capacity) form (`PUT /api/investing/profile`); scores, categories, emergency months and the limiting factor in plain words, with the cap that applies |
| `components/holdings-editor.tsx` | One row per asset class plus "Add another" for more labelled holdings in a class (`PUT /api/investing/holdings` replaces the list); current weights |
| `components/investment-plan-result.tsx` | One `InvestmentPlan`: badges, warnings, allocation + rebalance table, SIP card and what it was sized from, projection, instrument types, `AssumptionsPanel`, explanation with `ExplanationBadge` |
| `components/allocation-chart.tsx`, `components/projection-chart.tsx` | Recharts grouped bars: target vs current %; bad / typical / good (p10 / p50 / p90) per SIP with a dashed goal line |
| `components/plan-history-list.tsx` | `GET /api/investing/plans`; a click opens the full plan with `GET /api/investing/plans/{id}` |
| `components/what-if-simulator.tsx` | `POST /api/investing/simulate` (nothing stored); category defaults to the profile's; shows the assumptions in force |

## Common mistakes
- Calling `fetch` directly from a page (use `lib/api.ts`).
- Setting `Content-Type` for `FormData` uploads.
- Thinking the auth guard is security. The backend's 401 is.

## How to test (F0)
`npm run lint` and `npm run build` must pass. Manual: the home page shows "Connected" with the backend up,
and a readable "Network error" with it stopped.

## Budgets page notes (P8)
- `GET /api/profile/financial` answers **404 until a profile is saved**. The page treats that as "not set yet", not
  as an error (the 404 still shows in the raw panel, which is expected).
- The backend's `percent_used` is **0-100**; `formatPercent()` takes a fraction, so the page divides by 100.
- After an override, the row's reasoning still describes the planner's number; the table shows
  "planner: ₹X" under your limit so the two make sense together.

## How to test (/budgets)
`npm run lint` and `npm run build` must pass. Manual, as a fresh synthetic user at http://localhost:3000/budgets:
1. Profile shows "not set yet" with no red error; Generate is disabled.
2. "Suggest from my income" with no statements → message, no number. After uploading a fixture with salary
   credits, pick a month after it → a suggestion and per-month incomes; "Use this" fills the field only.
3. Save 60000 / 20 → savings ₹12,000, spendable ₹48,000. Generate for a month with no earlier expenses → method
   `baseline`, total of limits = ₹48,000. With earlier categorized expenses → `history` and data-quality counts.
4. Edit a limit (e.g. 4500, Enter) → badge `user` and "planner: ₹…"; Generate again → your limit stays; Reset → `agent`.
5. Limit `-5` or `12.345` is rejected in the form before any call.
6. Status: ≥ 80 % amber, > 100 % red (bar and chart); investments appear only under "Invested this month".

## Forecast page notes (F10)
- **Opening balance prefill:** the page reads `GET /api/transactions/?limit=500` (newest first, the same order the
  backend uses) and shows the first row's `balance`. If you do **not** edit it, the request omits `opening_balance`,
  so the backend reads the statement itself and the result says "statement of <date>". Typing a value sends it
  ("typed by you"); "Use statement balance" undoes the edit. No balance anywhere → the backend's 400 is shown.
- `GET /api/forecast/latest?horizon_days=` answers **404 until the first run of that horizon**. The page shows
  "No 30-day forecast yet", not an error (the 404 still appears in the raw panel, which is expected).
- `useApiData` keeps the previous data when a new request fails, so the page only shows a run whose
  `horizon_days` matches the selected tab (otherwise the 30-day run would stay visible under "60 days").
- The band is the 80% range of the balance; the cash-crunch date is read from its **lower** edge
  (`cash_crunch_basis="lower"`), so it is the cautious estimate.
- Dates from the API (`YYYY-MM-DD`) are formatted with `formatDay()`, which parses them as local dates.

## How to test (/forecast)
`npm run lint` and `npm run build` must pass. Manual, as a fresh synthetic user at http://localhost:3000/forecast:
1. Before any upload: the hint says "No statement balance found", recurring shows its empty state, and the
   result card says "No 30-day forecast yet" with no red error.
2. Upload `backend/tests/fixtures/statements/messy.csv` on /upload, come back: the input shows ₹42,202.90
   "From your statement of 30 Sep 2026".
3. Generate 30 days: 30-day chart with a band, "statement of 30 Sep 2026" on the opening-balance card,
   end balance = opening + net cash flow, the low-confidence notice (1 month of history).
4. Switch to 60: "No 60-day forecast yet". Type 100 → Generate: "typed by you", 60 days, a cash-crunch banner and
   a red vertical line on the chart. Switch back to 30: the earlier 30-day run is shown again.
5. Type `12.345` → a readable error before any call (nothing new in the raw panel).
6. With an LLM key: badge "explanation: AI · guard passed"; without one: "explanation: template".
7. The recurring table fills only after 3+ similar monthly payments (the fixtures are too short, by design).

## Investing page notes (F11)
- `GET /api/investing/profile` and `GET /api/investing/plan/latest` answer **404 until the first save**. The page
  shows "No risk profile yet" / "No plan yet", not an error (the 404s still appear in the raw panel, as expected).
- **Generate** is disabled until a profile exists; a 409 from the backend (profile missing) is still shown with a
  pointer to section 1. Goal years without a goal amount, or bad numbers, are caught before any call.
- The **holdings editor** sends every row with an amount (an empty amount removes that holding), because the PUT
  replaces the whole list. Two rows of one class need different labels (the backend's unique rule).
- **Badge:** "AI-written · guards passed" only when `explanation_source == "llm"` (LLM used AND the numeric and
  claims guards passed); "Template (AI text rejected by a guard)" when the LLM ran but failed a guard; otherwise
  "Template". The explanation already ends with the disclaimer (added by code); every section also prints the
  `disclaimer` field of its response.
- The **assumptions panel** of a plan shows the assumptions stored WITH that plan (an old plan keeps its own); the
  simulator shows the ones in force now (`GET /api/investing/assumptions`).
- **Projections start from ₹0**: they cover the monthly SIP only, not the current holdings (the page says so).
- Chart colours are the console's grayscale chart tokens (the neutral Shadcn theme, like every other chart):
  "Current / Existing" is `--chart-2`, "Target / Recommended" is `--chart-5` in light mode and `--chart-1` in dark
  mode (a per-theme `ChartConfig`). The dataviz palette validator passes them on colour-blind separation,
  normal-vision separation and contrast in both modes; a legend names both series and the tables / tiles carry
  the exact numbers.

## How to test (/investing)
`npm run lint` and `npm run build` must pass. Manual, as a fresh synthetic user at http://localhost:3000/investing:
1. Before anything: the banner is shown; sections 1, 2, 4 show their empty states with no red error; Generate is
   disabled with "Save your risk profile (section 1) first"; the history says "No saved plans yet".
2. Save a profile with answers 5/5/5 and liquid savings 10000, income stability 4, 0 dependents, 10 years: category moderate (not aggressive), limiting
   factor "Your finances, not your comfort with risk, are the limit right now", and (no spending data yet) the
   hint that essential spending is unknown.
3. Holdings: equity 50000 "index", Add another, equity 20000 "flexi", debt 30000. Save: weights 70% / 30% / 0% / 0%.
   Give both equity rows the same label: a readable error before any call.
4. Generate with goal 500000 in 5 years: warnings `forecast_missing` (and `surplus_unknown` / `essentials_unknown`
   for a user with no data), chart + rebalance table, SIP card, projection tiles with the chance of the goal.
   Upload `backend/tests/fixtures/statements/messy.csv`, generate a 30-day forecast on /forecast, then generate
   again: `forecast_missing` disappears and the surplus says "low end of your 30-day forecast".
5. History: two plans; click the older one: it is shown in full (the goal of 500000 comes back) and highlighted.
6. Simulator: 10000 a month, 10 years, moderate, goal 2000000 → p10 ≤ p50 ≤ p90, chance of the goal, and the
   history does NOT grow (nothing stored).
7. Narrow the window to phone width: grids collapse to one or two columns, tables scroll inside their card.

## Transactions, review, summary and rules notes (F3-F6)
- **Editing a category** (pencil) opens an editor row under the transaction. The dropdown offers only labels that
  fit the row's direction (debit or credit lists from `GET /api/categories/`). Saving locks the row
  (`source=user`, lock icon) and keeps the agent's original label, shown as "agent said: …".
  - "Apply to similar" also relabels your **unlocked** rows of the same payee; "Remember this payee" creates a
    payee rule. Either one may change other rows, so the page refetches; otherwise only that row is swapped.
  - The lock icon unlocks (the category stays; the agent may redo it on "Re-run, replacing AI results").
- **Radix Select** cannot have an item whose value is `""`, so "any" options use the `ANY` sentinel from
  `category-select.tsx`, and a label in both lists (e.g. "Transfers") is shown once.
- **Review:** every fix makes a row leave the queue, so any change refetches it and clears the selection.
  Changing page also clears the selection (never act on rows you cannot see). Bulk: rows the label does not fit
  are skipped by the backend and listed by id.
- **Summary:** only `txn_type=expense` is spending. Investments, redemptions and own-account transfers have their
  own tiles. The "Uncategorized" bar is amber. The page only shows data for the selected month (no stale month).
- **Rules:** there is no "add rule" button by design; rules come from "Remember this payee". Deleting a rule
  only affects future categorization.

## How to test (/transactions, /review, /summary, /rules)
`npm run lint` and `npm run build` must pass. Manual, as a fresh synthetic user:
1. Upload `backend/tests/fixtures/statements/messy.csv`, then open `/summary` for **2026-09**: everything is
   "Uncategorized" (amber).
2. `/transactions` → Categorize: rows get `rule` / `llm` badges and confidence bars; hover ⓘ for the reasoning.
3. Filters: Type = Expense, From 2026-09-01 To 2026-09-30 → 16 rows; "Clear filters" resets; paging shows
   "Showing 1–… of …".
4. Pencil on an expense → choose "Shopping", tick "Remember this payee" → Save: notice with the payee rule, a lock
   icon, "agent said: …". The dropdown has no income labels (Salary etc.) for a debit.
5. Click the lock → unlocked, category kept.
6. `/review`: 3 person-to-person transfers (guarded from the LLM, 50 %). Tick 2, choose "Transfers" → Apply:
   "Updated 2 rows", they leave the queue. Choosing an income label for a debit row reports it as skipped.
7. `/rules`: the rule from step 4 → Delete → Confirm delete → gone; the transaction keeps "Shopping".
8. `/summary` 2026-09 again: categories now filled; income vs expenses vs investments chart; savings rate ≈ 55 %.
