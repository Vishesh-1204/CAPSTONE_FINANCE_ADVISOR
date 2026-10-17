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
- Done: Phase 4 backend P0-P7 (spec docs/phase4-spec.md) on branch feature/vishesh-budget, 363 tests passing:
  migration 0003 (financial_profiles, budgets, budget_runs), Decimal calculator (Python computes, LLM only
  explains, one call per plan), explanation guard + template fallback, profile/budget/status routes,
  GET /api/profile/financial/suggestion (refunds excluded), synthetic 6-month 3-persona history (seed 42),
  evaluation/evaluate_budget.py -> backend/evaluation/budget_metrics.csv + .md (committed, deterministic;
  B1 MAE Rs 964 vs B0 Rs 1,621). Docs: docs/modules/budget-planning.md, budget-evaluation.md.
- In progress: S7 frontend Test Console (frontend/, npm run dev on the host). F0 done (scaffold, lib/api.ts,
  shared components, home = connection check, backend GET /api/categories). F1-F6 pages not built yet:
  /login, /upload, /transactions, /review, /summary, /rules (see docs/modules/frontend-console.md).
- Optional, ask first: S9 Celery + Redis background import/categorize.
- Next: P8 frontend /budgets page (separate session, spec section 8), finish S7 pages, then Phase 5 Forecast
  (B1 history-average is its baseline; reuse the evaluate_budget.py split).
- Known gaps: no HuggingFace/Llama provider in llm/factory.py yet (approved stack); person guard recall 0.857
  (1 person-like string reached the LLM stage in the offline eval); budget guard only checks amounts with a
  currency marker; B2 (--llm real) faithfulness not measured yet; synthetic personas do not react to budgets.

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
- Evaluation (offline):    docker compose exec backend python -m evaluation.evaluate_categorizer
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