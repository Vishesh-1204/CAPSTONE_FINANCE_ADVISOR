# Module: Frontend Test Console (S7) — IN PROGRESS

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
| F3 | `/transactions` (filters, paging, source badges, confidence bar, reasoning tooltip, lock, inline edit with "apply to similar" / "remember this payee") | not started |
| F4 | `/review` (needs-review queue, bulk select + bulk category) | not started |
| F5 | `/summary` (Recharts: expense by category; income vs expense vs investment) | not started |
| F6 | `/rules` (list / delete payee rules) | not started |
| P8 | `/budgets` (Phase 4: financial profile + income suggestion, generate plan, limits table with inline override / reset, status bars, Recharts spent vs limit) | **done** |

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
| `app/(console)/budgets/page.tsx` | `/budgets`: owns the month and three loads (profile, limits, status); refetches after every change |
| `components/financial-profile-card.tsx` | Income + savings % form (`PUT /api/profile/financial`), "Suggest from my income" (fills the field, saves nothing) |
| `components/budget-plan-card.tsx` | Result of `POST /api/budgets/generate`: method, summary, feasibility, LLM used / guard result, data quality. Only shown right after generating (the summary is not stored) |
| `components/budget-table.tsx` | Limits with source badge and reasoning tooltip; inline edit (`PUT /api/budgets/{id}`) and reset (`POST .../reset`) |
| `components/budget-status-card.tsx` | `GET /api/budgets/status`: totals, progress bars by status, unbudgeted spending, Recharts spent vs limit |

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
