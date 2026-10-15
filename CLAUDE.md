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
- Done: Phase 3B v2 (hybrid categorizer, batch LLM, memory, rules, CSV import, 63 tests) applied and verified.
  memory from user corrections, better CSV import, evaluation harness, 63 unit tests.
  The zip's top-level folder is backend/, so it must be extracted at the REPO ROOT, never inside backend/
  (otherwise you get backend/backend/).
- Next: Phase 4 Budget Planning Agent. Adopt Alembic migrations at the start of Phase 4.

## Architecture (clean layers: each layer only calls the one below it)
backend/app/
- api/routes/   HTTP only: parse request, call a service, return response. No business logic.
- services/     business logic (categorization, budgets, CSV import)
- agents/       LLM reasoning only; knows nothing about the DB or HTTP
- llm/          provider abstraction (base.py, gemini_provider.py, openai_provider.py, factory.py).
                Never import a vendor SDK outside this folder. Provider chosen by LLM_PROVIDER in .env.
- models/       SQLAlchemy tables.  schemas/  Pydantic request/response shapes.
- core/         config, security, logging, shared constants (categories live in core/categories.py after v2)
- db/           engine/session. Tables are created with create_all (no Alembic yet).
Security rule: user_id ALWAYS comes from the JWT (get_current_user), never from the request body.
Every query that touches user data must filter by user_id.

## Commands (Windows PowerShell, run from the repo root)
- Start everything:        docker compose up --build
- Wipe the DB (test data): docker compose down -v      (needed after adding columns, until Alembic)
- Run unit tests:          docker compose exec backend python -m pytest tests -q
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