# Architecture

## 1. The big picture

The system is a FastAPI backend with a PostgreSQL database, running in Docker, plus a Next.js
frontend run on the host. Phase 3B delivers the **Expense Intelligence Agent**: it reads bank
statements, works out what kind of money movement each line is (expense, income, investment,
transfer), assigns a category, and learns from the user's corrections. Phase 4 adds the **Budget
Planning Agent**: a rules engine turns the categorized expenses into monthly limits, and one LLM call
explains them (see [modules/budget-planning.md](modules/budget-planning.md)). Phase 5 adds the **Financial
Forecast Agent** ([modules/forecasting.md](modules/forecasting.md)) and Phase 6 the **Investment Advisor Agent**
([modules/investing.md](modules/investing.md)): a risk profile, a target allocation and an affordable SIP sized
from the forecast's lower band, explained by one guarded LLM call. Educational, not financial advice.

```mermaid
flowchart LR
    Browser["Next.js Test Console<br/>(npm run dev, :3000)"] -- "HTTP + JWT<br/>(CORS)" --> API
    Swagger["Swagger UI<br/>:8000/docs"] -- "HTTP + JWT" --> API
    subgraph Docker
        API["FastAPI backend :8000"] --> DB[("PostgreSQL 16")]
    end
    API -- "cleaned merchant strings only;<br/>budgets, forecast, investing: aggregates only" --> LLM["LLM provider<br/>(Gemini dev / OpenAI)"]
```

## 2. Layers (each layer only calls the one below it)

```
api/routes/   HTTP only: parse the request, call ONE service function, return the response
   │
services/     business logic: import, enrich, categorize, override, summarize
   │      └──► agents/   LLM reasoning only (prompt, batching, retries); no DB, no HTTP
   │                └──► llm/   provider abstraction; the ONLY place a vendor SDK is imported
models/ + db/ SQLAlchemy tables and the session
core/         config, security, constants shared by everyone (categories, txn types, dictionary)
schemas/      Pydantic request/response shapes (the API contract)
```

Why it matters: a route never contains logic, so the same logic can be called from a test,
the evaluation harness or (Phase 9) a Celery task. The agent never touches the database, so it
can be tested with a fake LLM and swapped without touching storage.

**Security rule:** `user_id` always comes from the verified JWT (`api/deps.py:get_current_user`),
never from the request body, and every query on user data filters by `user_id`. Someone else's
id therefore looks exactly like a missing id (404), which reveals nothing.

## 3. Folder map (`backend/`)

| Path | Contents |
|---|---|
| `app/main.py` | Creates the app, CORS middleware, registers routers, `/health` |
| `app/api/deps.py` | `get_current_user` (JWT → `User`) |
| `app/api/routes/` | `auth`, `statements`, `transactions`, `payee_rules`, `categories`, `profile`, `budgets`, `forecast`, `investing` |
| `app/services/` | All business logic (see the module pages) |
| `app/services/statement_parsers/` | `base.py` (interface), `csv_parser`, `xlsx_parser`, `pdf_parser`, `tabular.py` (shared) |
| `app/agents/expense_categorizer.py` | The LLM layer of the categorizer |
| `app/services/forecasting/` | Phase 5 forecast building blocks: pure maths (models, backtest, intervals, recurrence, horizon, cash-flow projector) plus `data_preparation.py`, the only one that reads the DB |
| `app/agents/budget_planner.py`, `explanation_guard.py` | Budget explanations (one LLM call, template fallback) and the faithfulness guard |
| `app/agents/forecast_explainer.py` | Forecast explanation (template, one LLM call, the same guard) |
| `app/services/investing/` | Phase 6 building blocks, pure: risk scoring, allocation, portfolio stats, SIP planner, Monte Carlo, claims guard, instrument catalog; `assumptions.py` reads the `INVEST_*` settings |
| `app/services/investment_service.py` | Phase 6 orchestration: profile, holdings, forecast surplus, essentials, plan, simulate |
| `app/agents/investment_explainer.py` | Investment explanation: pre-formatted facts, one LLM call, rupee + percentage + lakh/crore + claims guards, disclaimer added by code |
| `app/llm/` | `base.py`, `factory.py`, `gemini_provider.py`, `openai_provider.py` |
| `app/core/` | `config.py`, `security.py`, `categories.py`, `transaction_types.py`, `merchant_dictionary.py`, `money.py`, `month_utils.py`, `disclaimers.py`, `logging_config.py` |
| `app/models/`, `app/schemas/` | Tables and API shapes |
| `migrations/` | Alembic: `0001_baseline`, `0002_statement_ingestion_and_overrides`, `0003_budget_planning`, `0004_forecast_runs`, `0005_investing` |
| `evaluation/` | Research harnesses (`evaluate_categorizer.py`, `evaluate_budget.py`, `evaluate_forecast.py`, `evaluate_investing.py` with `investing_rules.py`, `investing_personas.py`, `investing_methods.py`), datasets, `budget_metrics.csv`, `forecast_metrics.csv`, `investing_metrics.csv`, `results/` (gitignored) |
| `scripts/make_synthetic_statements.py` | Generates the synthetic PDF/XLSX/CSV fixtures (dev only) |
| `scripts/make_synthetic_history.py` | Generates the seeded 6-month, 3-persona history for the budget evaluation |
| `scripts/make_synthetic_panel.py` | Generates the seeded 24-month, 30-user, 4-persona panel for the forecast evaluation |
| `scripts/smoke_test.py` | Live end-to-end check of the running API (16 steps) |
| `tests/` | 745 unit and HTTP tests (in-memory SQLite, fake LLM) |

## 4. Flow 1: upload a statement

```mermaid
sequenceDiagram
    actor U as User
    participant R as routes/statements.py
    participant S as statement_import_service
    participant P as Parser (CSV / XLSX / PDF)
    participant T as tabular.assemble_rows
    participant E as transaction_enrichment
    participant DB as PostgreSQL

    U->>R: POST /api/statements/upload (file, password?)
    R->>R: size check (≤ 10 MB)
    R->>S: import_statement(user_id from JWT, bytes, password)
    S->>P: get_parser(extension).parse(bytes, password)
    P->>T: grid of cells
    T-->>S: ParseResult(rows, invalid, layout, opening balance)
    S->>S: check_balances (prev − debit + credit = balance ± 0.01)
    S->>DB: load existing rows in the date range (dedupe keys)
    S->>DB: INSERT import_batches
    loop each new row
        S->>E: enrich_transaction (channel, payee_key, txn_type; local)
    end
    S->>DB: INSERT transactions, COMMIT
    S-->>U: import summary (counts, by_txn_type, balance check, next_step)
```

Rows are saved **uncategorized**. Categorization is a separate call, so a large upload never
waits for the LLM and a failed LLM call never loses imported data.

## 5. Flow 2: categorize, then review

```mermaid
flowchart TD
    A["POST /api/transactions/categorize"] --> B["pending = uncategorized AND NOT user_locked"]
    B --> C{"Payee rule?<br/>(UPI ID, then merchant key)"}
    C -- yes --> Z["save: source=payee_rule, conf 1.0"]
    C -- no --> D{"Memory?<br/>(user-locked row with same<br/>direction + description_key)"}
    D -- yes --> Z2["save: source=memory, conf 1.0"]
    D -- no --> E{"Type dictionary?<br/>(investment / self-transfer / any credit)"}
    E -- yes --> Z3["save: source=rule, conf 0.95<br/>(unknown credit: Other Income, 0.5)"]
    E -- no --> F{"Expense keyword rule?<br/>(exactly one category)"}
    F -- yes --> Z3
    F -- no --> G{"Looks like a person?<br/>(UPI/IMPS/NEFT, name-like)"}
    G -- yes --> Z4["Transfers + needs_review<br/>NEVER sent to the LLM"]
    G -- no --> H["LLM: batches of 20 CLEANED strings<br/>primary → fallback provider"]
    H -- answered --> Z5["save: source=llm<br/>needs_review if conf < 0.60"]
    H -- failed --> Z6["left UNCATEGORIZED<br/>(retried next run, never 'Other')"]
    Z5 --> R["Review queue: GET /transactions?status=needs_review"]
    Z4 --> R
    R --> O["User: PATCH /{id}/category or POST /bulk-category<br/>→ locked, becomes memory, optional payee rule"]
```

## 6. Privacy boundary

Everything left of the LLM arrow in section 1 is local. Specifically:

- Parsing (including PDF passwords) is 100% local. Passwords are never stored or logged.
- The LLM receives only `description_key` (lowercased, no digits, no UPI handles, no rail words),
  the median amount of that group and how many rows share it. It never sees raw narrations, UPI IDs,
  reference numbers, balances, account numbers, or person-like payees.
- Few-shot examples sent to the LLM are the user's past corrections, cleaned the same way, with
  person-like payees removed.
- Logs contain counts and ids, never narrations.
- The budget planner sends only category names and aggregate numbers (limits, averages, income,
  savings, spendable). Never a description, payee or UPI ID (tested).
- The investment explainer sends only pre-formatted aggregates (categories, percentages, SIP and range
  amounts, instrument TYPES). Never holding labels or transactions (tested).
- **Measured limitation:** the person guard is a heuristic. In the latest offline evaluation it had
  recall 0.857, and 1 person-like string out of the labeled set still reached the LLM stage (see
  [modules/evaluation.md](modules/evaluation.md)). Use synthetic data with the free Gemini tier.

## 7. Key design decisions

| Decision | Why |
|---|---|
| Cheapest-first priority (lock → payee rule → memory → rules → guard → LLM) | Fewer LLM calls (0.88 per 100 rows offline), deterministic and explainable for most rows, and the rule layer doubles as the research baseline |
| `txn_type` separate from `category` | A SIP is not spending. Budgets (Phase 4) count only `txn_type='expense'` |
| Strategy pattern for parsers + one shared pipeline | A new format = one class; dedupe, balance check and enrichment are never duplicated |
| `amount` is `Numeric(14,2)`, Python `Decimal` | Floats drift by paise when summed |
| Alembic migrations; app no longer calls `create_all` | Schema changes without wiping the DB; the drift test catches a forgotten migration |
| `agent_category` kept when the user overrides | Measures override rate (feedback metric for the paper) |
| No `status` column; status is a computed filter | One less column to keep consistent |
| Budgets: Python computes, the LLM only explains (one call per plan) | Limits are guaranteed to fit the income and are unit-testable; the guard rejects any invented amount; it works with no API key |
| `agent_limit_amount` kept when the user overrides a limit | Reset, and the override-rate metric |
| Provider abstraction + fallback chain | Swap Gemini/OpenAI by config; a quota error on one provider does not stop the run |
| Forecast = recurring items on their dates + a simple per-user model for the rest | Short histories overfit deep models; each part is explainable ("rent leaves on the 5th") and testable |
| Per-user backtest selection, Student-t interval from the model's own errors, `low_confidence` flag | Honest uncertainty instead of a black box; measurable coverage for the paper |
| Shared SQL filters (`transaction_filters.py`) for spending/income | The Budget and Forecast agents can never disagree on what counts as spending |
| Investing: risk = the safer of tolerance (questionnaire) and capacity (finances) | A brave person with no emergency fund is not told to go aggressive; `limiting_factor` says why |
| SIP sized from the forecast's LOWER band x 0.8, after an emergency top-up | One bad month should not break the plan; ties Phase 5 uncertainty to Phase 6 |
| Numbers pre-formatted for the LLM, four guards, disclaimer added by code after them | The model copies instead of re-rounding; promises and invented numbers fall back to the template |
| Claims checker injected into the explainer by the service | Agents must not import services; `claims_guard.py` stays with the other pure investing modules |

## 8. Known gaps (carry into later phases)

- `LLM_PROVIDER` currently supports `gemini` and `openai` only. A HuggingFace/Llama provider is part
  of the approved stack but not yet in `llm/factory.py:PROVIDER_REGISTRY`.
- Categorization is synchronous (a large batch with many LLM calls blocks the request). Phase 9 / optional S9 moves it to Celery.
- Investment **returns** cannot be computed from a bank statement; holdings are typed in by hand (Phase 6).
- Investing: return assumptions are illustrative placeholders (to be cited before publication); normal returns
  understate fat tails; the claims guard misses promises worded without its trigger words and flags every
  negation on purpose; on the Phase 5 panel a SIP from the lower band x 0.8 still exceeds the actual next-month
  surplus about 28% of the time (see [modules/investing-evaluation.md](modules/investing-evaluation.md)).
- Scanned PDFs are rejected (no OCR).
- The budget guard only checks amounts with a currency marker (₹ / Rs. / INR); a bare "5000" is not checked.
- The budget evaluation uses synthetic people who do not react to their budget (see [modules/budget-evaluation.md](modules/budget-evaluation.md)).
- Forecast: income is treated as known (no band), intervals cover about 61-65% instead of 80%, backtest selection
  does not beat the best fixed model on the synthetic panel, own-account transfers are not forecast (see
  [modules/forecast-evaluation.md](modules/forecast-evaluation.md)).
- The frontend Test Console is a testing aid (token in `localStorage`); the Phase 12 dashboard replaces it (see [modules/frontend-console.md](modules/frontend-console.md)).
