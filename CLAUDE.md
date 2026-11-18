# Agentic AI-Based Autonomous Personal Finance Advisor

Final-year B.Tech capstone by a 4-person team: Vishesh (architecture and integration lead), Priti, Anvisha, Dev.
Goals: capstone grade, resume/portfolio, IEEE research paper. Prioritise scalability, clean architecture,
research value, explainability and maintainability over quick fixes.

## How to help me (the developer)
- I am a beginner-to-intermediate Python developer who wants to UNDERSTAND every component, not copy blindly.
- Explain in beginner-friendly language. For any new component say: why it exists, where it fits, what the
  common beginner mistakes are, how to test it, and what it connects to next.
- Prefer Plan mode for multi-file changes: show the plan first, then edit.
- For a small fix, be brief. For a NEW component or phase, use this order:
  1 Overview, 2 Why it is needed, 3 Folder location, 4 Complete code, 5 Code explanation,
  6 Common beginner mistakes, 7 Testing steps, 8 Next steps.
- Think like a researcher too: point out baselines, ablations, metrics and datasets where relevant.

## Approved tech stack (do NOT add anything else without asking first and explaining why)
- Frontend: Next.js, React, TypeScript, Tailwind CSS, Shadcn UI, Recharts
- Backend: FastAPI, Python, SQLAlchemy, Celery, Redis
- Database: PostgreSQL, Qdrant
- AI: LangGraph, LangChain, OpenAI models, Llama models, HuggingFace
  (Gemini free tier is used for development through the provider abstraction; it is NOT in the approved list,
  so keep OpenAI and Llama/HuggingFace working as first-class providers)
- ML: scikit-learn, XGBoost, LightGBM, PyTorch. NLP: Sentence Transformers, FinBERT
- Deployment: Docker, GitHub Actions, AWS / Azure / Render

## Phases (never jump ahead of the current one)
1 Setup, 2 Auth, 3 Expense Intelligence Agent, 4 Budget Planning, 5 Forecast, 6 Investment Advisor,
7 Stock Intelligence, 8 Goal Planning, 9 Automation (Celery), 10 Financial Coach,
11 LangGraph orchestration, 12 Frontend dashboard, 13 Deployment.

## Current status (update this when it changes)
- Done: Phase 1 (setup), Phase 2 (auth, tested), Phase 3A/3B (transactions, CSV upload, LLM categorizer).
- Done: Phase 3B v2 (hybrid categorizer, batch LLM, memory, rules, CSV import, 63 tests).
- Done: Phase 3B v3 (spec docs/phase3b-v3-spec.md) on branch feature/vishesh-3b-v3, 223 tests passing:
  S1 Alembic (0001 baseline, 0002 v3; `alembic upgrade head` runs before uvicorn), S2 direction/txn_type,
  investment dictionary, Numeric amounts, S3 payee rules, locks, agent_category, bulk, unlock, review-queue
  filters, summary, person-payee guard, LLM fallback chain, S4/S5 CSV/XLSX/PDF parsers (password, scanned 422,
  balance check, import batches), S6 evaluation on the real pipeline (metrics CSV in backend/evaluation/results/,
  gitignored), S8 docs in docs/ (architecture, API, database, testing, one page per module).
- Done: Phase 4 COMPLETE (P0-P8, spec docs/phase4-spec.md) on branch feature/vishesh-budget, 363 tests passing,
  live smoke test 12/12 (backend/scripts/smoke_test.py, see docs/testing.md). Backend P0-P7:
  migration 0003 (financial_profiles, budgets, budget_runs), Decimal calculator (Python computes, LLM only
  explains, one call per plan), explanation guard + template fallback, profile/budget/status routes,
  GET /api/profile/financial/suggestion (refunds excluded), synthetic 6-month 3-persona history (seed 42),
  evaluation/evaluate_budget.py -> backend/evaluation/budget_metrics.csv + .md (committed, deterministic;
  B1 MAE Rs 964 vs B0 Rs 1,621). Docs: docs/modules/budget-planning.md, budget-evaluation.md.
  P8 frontend /budgets page (profile + income suggestion, generate, limits table with inline
  override/reset, status bars, Recharts spent vs limit, raw panel). Lookback ablation is 1/2/3 (6 would read
  the test months; see docs/modules/budget-evaluation.md).
- Done: S7 frontend Test Console (frontend/, npm run dev on the host): F0 scaffold + lib/api.ts, F1 /login,
  F2 /upload, F3 /transactions (filters, paging, badges, inline edit + apply-to-similar / remember-payee, unlock),
  F4 /review (queue + bulk category), F5 /summary (Recharts), F6 /rules (list/delete), on branch
  feature/vishesh-console-pages (stacked on feature/vishesh-forecast). See docs/modules/frontend-console.md.
- Done: Phase 5 core (F0-F7 + F6.1, spec docs/phase5-spec.md) on branch feature/vishesh-forecast, 541 tests
  passing, live smoke test 15/15. Migration 0004 (forecast_runs), services/forecasting/ (pure models, backtest,
  Student-t horizon-sum intervals, recurrence detector, horizon weights, cash-flow projector; data_preparation.py
  is the only DB reader), forecast_service + /api/forecast/{generate,latest,history,recurring,runs/{id}},
  agents/forecast_explainer.py (template, one LLM call, Phase 4 guard), budget-overspend warnings.
  F6: scripts/make_synthetic_panel.py (24 months, 30 users, 4 personas, seed 42; CSV not committed, SHA-256 in
  the results) + evaluation/evaluate_forecast.py (rolling origin, 15 origins x 3 horizons = 1,350 forecasts via
  the real service) -> backend/evaluation/forecast_metrics.csv + .md (committed, deterministic).
  F6.1 (found by F6): recurrence frequency check (precision 56% -> 94%, FORECAST_RECURRING_FREQUENCY_CHECK) and
  FORECAST_INTERVAL_METHOD=student_t (coverage 47% -> 61-65%; empirical/conformal kept as ablations).
  Docs: docs/modules/forecasting.md, forecast-evaluation.md; api/database/testing/architecture updated.
- Optional, ask first: S9 Celery + Redis background import/categorize; Phase 5 F8 GET /api/forecast/accuracy;
  F9 global LightGBM model (adds scikit-learn + lightgbm).
- Done: Phase 5 F10 frontend /forecast page (horizon toggle, statement-balance prefill, Recharts balance + 80% band,
  cash-crunch banner, low-confidence notice, template/AI badge, budget warnings, recurring table, raw panel).
- Done: Phase 6A + 6B backend (spec docs/phase6-spec.md, decisions in its section 14) on branch
  feature/vishesh-investing, 745 tests passing, live smoke test 16/16. Migration 0005 (investment_profiles,
  holdings with CHECK + NOT NULL label, investment_plans with (user_id, created_at DESC) index), pure modules in
  services/investing/ (risk scoring, allocation, portfolio stats, SIP planner, Monte Carlo, claims guard, catalog,
  assumptions from INVEST_* settings = illustrative placeholders, cite before publishing), investment_service
  (lower-band surplus from the latest 30-day forecast, essentials via budget_service.average_essential_spending,
  warnings forecast_missing/forecast_stale/surplus_unknown/essentials_unknown/no_holdings), agents/
  investment_explainer.py (pre-formatted facts with Indian digit grouping, rupee + percentage + lakh/crore +
  claims guards, claims checker injected by the service, disclaimer from core/disclaimers.py added after the
  guards), /api/investing/* (9 routes). 6B: evaluation/investing_rules.py (pre-registered, committed alone first),
  investing_personas.py (500, seed 42), investing_methods.py (B0, B1, B2, P, two mixed variants, 7 ablations),
  evaluate_investing.py -> backend/evaluation/investing_metrics.csv + .md (2,000 paths, committed). Engine 6.1
  (one round of fixes after the 6.0 run, logged in docs/modules/investing-evaluation.md): P breaks R4 0.0% (was
  15.6%; top-up now capped at the surplus) and R5 0.0% (was 58.5%; fragile-household cap), allocation rules broken
  0.0% (was 16.6%), vs 100% R4 for the naive-SIP baselines. R3a 18.2% is a trade-off (relabelled from enforced
  after seeing results): all 22 violations are infeasible given the surplus (0 avoidable). Panel replay: SIP from
  low band x 0.8 exceeds the actual next-month surplus in 28.4% of 450 forecasts vs 47.3% for the mean surplus.
  Docs: docs/modules/investing.md, investing-evaluation.md; api/database/testing/architecture updated.
- Done: Phase 6C frontend /investing page (F11, docs/modules/frontend-console.md) + GET /api/investing/plans/{plan_id}
  for the plan history, 771 tests passing, smoke 16/16.
- In progress: Phase 7 (Stock Intelligence, spec docs/phase7-spec.md) on branch feature/vishesh-stocks,
  1209 tests passing + 1 skipped (real FinBERT, runs in the ml image), live smoke test 17/17 (also passes
  with no internet from the seeded cache). Done: 7A-0 spike (docs/phase7-provider-spike.md: yfinance prices,
  Google News RSS headlines, Alpha Vantage free tier = 100 bars only, not used), 7A-1 pure modules
  (math.fsum fix for Python 3.11), 7A-2 migration 0006 (watchlist_items, price_bars Numeric(14,4), news_items,
  stock_analyses, provider_calls) + strict symbol validator (core/stock_symbols.py), 7A-3 market_data/
  (yfinance adapter = the ONLY yfinance import, approved 2026-11-09; RSS adapter; cache_service fallback chain
  fresh -> provider -> stale + data_stale -> CSV hint; provider cooldown; seed_market_cache.py),
  7A-4 sentiment/ (lexicon default, FinBERT lazy via requirements-ml.txt + `docker compose --profile ml up`
  on port 8001; model card licence CC BY-NC-SA 4.0, revision pinned), 7A-5 stock_service, stock_explainer
  (any-number guard + claims guard + no headlines to the LLM), /api/stocks/* (12 routes).
  7B evaluation (docs/modules/stocks-evaluation.md, stocks.md; api/database/testing/architecture updated):
  pre-registered evaluation/stocks_rules.py committed alone first; scikit-learn 1.5.2 + LightGBM 4.5.0 +
  scipy in requirements.txt (+ libgomp1 in the Dockerfile; default image 716 -> 943 MB); 10 hand-picked NSE
  large caps + ^NSEI to EVAL_END 2026-10-30 (SHA-256 recorded, no price data committed); datasets fetched by
  scripts/fetch_sentiment_datasets.py into evaluation/data/ (gitignored): twitter_fin (MIT, primary) and
  Financial PhraseBank (CC BY-NC-SA 3.0, FinBERT trained on it = contaminated); FiQA not used (gated / no
  licence). Final run in the ml image -> evaluation/stocks_metrics.csv + .md (committed, deterministic).
  Results: H1 supported (FinBERT - lexicon macro-F1 +0.091, CI +0.059..+0.120) but TF-IDF+LR beats FinBERT
  (+0.078); H2 not evaluable (max 24 sentiment days; rho 0.1 needs ~2,500); H3 not supported (pooled acc LR
  0.5064, LightGBM 0.5010 vs always-up 0.5070; 0/10 beat buy-and-hold after 10 bps; placebo and AR(1) power
  controls pass); H4 not supported, reversed (bootstrap p10 +1.2..+7.3% ABOVE normal; variance ratio 0.73,
  exploratory); H5a supported (>= 99.33% served in S1-S4); H5b not supported (35% vs 25% threshold,
  mis-specified). Engine 7.1 fixes (one round, before/after in the doc): claims guard v3.1 (set B written
  before the fix: recall 10% -> 20%, hard-neutral FP 40% -> 0%) and cooldown backoff 15 -> 240 min
  (STOCK_PROVIDER_COOLDOWN_MAX_MINUTES; S4 failed calls/100 58.2 -> 9.5). G3 --g3-only (Gemini, 30
  analyses): 0% raw advice/prediction, 6.7% fallback (malformed JSON); manual review: 6/28 texts put a correct
  number on the wrong quantity (the number guard cannot see this).
  7C frontend /stocks (F12, docs/modules/frontend-console.md): 9 sections + permanent banner, lib/stocks.ts
  (warning sentences = backend WARNING_TEXT), grey per-theme chart colours checked with the dataviz validator;
  POST /api/stocks/sentiment/score gained an optional `scorer` (lexicon | finbert) so lexicon and FinBERT show
  side by side (FinBERT only on the ml backend; NEXT_PUBLIC_API_URL=http://127.0.0.1:8001). lint + build pass.
  7D: POST /api/investing/simulate gained method="bootstrap" (default "normal"): ^NSEI complete-month returns from
  the CACHED price_bars only (current month dropped, newest unbroken run of months), rescaled to the portfolio's
  assumed mean/volatility (match_moments; raw equity returns would ignore the category), block_bootstrap_sip with
  INVEST_BOOTSTRAP_BLOCK_MONTHS=6; < INVEST_BOOTSTRAP_MIN_MONTHS=60 -> normal + "history_too_short". Response adds
  requested_method, method, history (months, start, end, raw index return/vol), warnings. /investing what-if has a
  Normal / Historical bootstrap toggle. Dev cache: 119 months (Nov 2016 - Oct 2026); bootstrap p10 1.6-2.5% above
  normal, p90 below (narrower, consistent with RQ4). Docs: docs/modules/investing.md (7D section).
  No teammate rules or guard sentences (decided 2026-10-23): all rules and guard sentences are written by
  the method's author, stated as a limitation.
  Phase 7 gaps: offline, the Phase 4-6 LLM explanation calls hang until timeout (smoke needed the
  *_USE_LLM_EXPLANATIONS=false switches); Gemini sometimes returns truncated JSON (template used);
  uploaded CSV bars are shared by all users (insert-only); news search by ticker code has no relevance filter;
  the regex claims guard misses most new advice wordings and negated predictions; the app's +/-0.15 headline
  label rule hurts FinBERT (argmax better; not changed, needs a training-split threshold); no headline history.
- Known gaps: no HuggingFace/Llama provider in llm/factory.py yet (approved stack); person guard recall 0.857
  (1 person-like string reached the LLM stage in the offline eval); budget guard only checks amounts with a
  currency marker; B2 (--llm real) faithfulness not measured yet; synthetic personas do not react to budgets.
  Forecast: backtest selection does not beat the best fixed model on the panel (regret +Rs 82 to +Rs 629);
  interval coverage 61-65% vs 80% target; income treated as known (freelancer net cash flow MAE ~Rs 49k);
  own-account transfers not forecast; forecast LLM faithfulness (--llm real) not measured yet.
  Investing: claims guard recall 67% / every negation flagged on the 60 hand-labelled sentences (none independent
  yet); goal SIP sized at the expected return (~50% chance by construction); R7 applies to 1 persona only
  (independent attribute draws); normal returns, no tax/fees/inflation.

## Architecture (clean layers: each layer only calls the one below it)
backend/app/
- api/routes/   HTTP only: parse request, call a service, return response. No business logic.
- services/     business logic (categorization, budgets, CSV import)
- agents/       LLM reasoning only; knows nothing about the DB or HTTP
- llm/          provider abstraction (base.py, gemini_provider.py, openai_provider.py, factory.py).
                Never import a vendor SDK outside this folder. Provider chosen by LLM_PROVIDER in .env.
- models/       SQLAlchemy tables.  schemas/  Pydantic request/response shapes.
- core/         config, security, logging, shared constants (categories.py, transaction_types.py, merchant_dictionary.py)
- db/           engine/session. The schema is managed ONLY by Alembic (backend/migrations/versions/);
                a model change needs a new revision (tests/test_migrations.py fails on drift).
frontend/       Next.js Test Console (S7). All backend calls go through frontend/lib/api.ts.
Full docs: docs/README.md.
Security rule: user_id ALWAYS comes from the JWT (get_current_user), never from the request body.
Every query that touches user data must filter by user_id.

## Commands (Windows PowerShell, run from the repo root)
- Start everything:        docker compose up --build
- Wipe the DB (test data): docker compose down -v      (only to reset data; migrations handle schema changes)
- New migration:           docker compose exec backend alembic revision -m "description"
- Run unit tests:          docker compose exec backend python -m pytest tests -q
- Smoke test (live API):   docker compose exec backend python -m scripts.smoke_test   (needs compose up)
- Evaluation (offline):    docker compose exec backend python -m evaluation.evaluate_categorizer
                           (also evaluate_budget, evaluate_forecast, evaluate_investing [--paths 500 for dev])
- Frontend (host):         cd frontend; npm run dev   -> open http://localhost:3000 (not 127.0.0.1, CORS)
- Manual API testing:      http://127.0.0.1:8000/docs (Swagger). Authorize with the login token.
- In PowerShell `curl` is an alias for Invoke-WebRequest. Use Swagger, or `curl.exe` with escaped quotes.

## Code standards
- Production-quality, SOLID, type hints, error handling, logging, secrets only in environment variables.
- Every new file starts with a comment giving the file path and its purpose; comment classes, functions
  and non-obvious logic.
- Never commit .env. Check `git status` before pushing.

## Git workflow
main <- DEV <- feature/<name>-<description>. Open pull requests into DEV (the branch name is uppercase DEV),
never push directly to main or DEV. Commit messages: short and descriptive.

## Privacy and research notes
- The free Gemini tier may use inputs to improve Google products. Use SYNTHETIC or anonymised data only.
  Never send raw bank statements, account numbers or personal names to an LLM.
- Research directions: rule-based vs LLM vs hybrid categorization, memory/few-shot from user corrections,
  confidence calibration, LLM calls per 100 transactions, budget forecast error vs rule baselines.

## Known gotchas
- Pin langchain-core to 0.3.63 (>=0.3.17): 0.3.15 conflicts with langchain-openai 0.2.8 and pip cannot resolve it.
- A failed LLM call must leave a transaction UNCATEGORIZED (so it is retried), never label it "Other".
- Digits are stripped when normalising descriptions, so "Shop 1" and "Shop 2" count as the same description.
