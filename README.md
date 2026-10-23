# Agentic AI-Based Autonomous Personal Finance Advisor

Final-year B.Tech capstone. Backend: FastAPI + PostgreSQL (in Docker). Frontend: Next.js (runs on your laptop).
This README is only commands: follow it top to bottom and the project runs. No other help needed.

| What | Where |
|---|---|
| Frontend (the app you use) | http://localhost:3000 |
| Backend API docs (Swagger) | http://127.0.0.1:8000/docs |
| Backend health check | http://127.0.0.1:8000/health |

Commands below are for **Windows PowerShell**. Mac/Linux differences are noted.

---

## 1. One-time setup (do this once per laptop)

Install these and restart your PC if the installer asks:

1. **Git**: https://git-scm.com/downloads
2. **Docker Desktop**: https://www.docker.com/products/docker-desktop/
   Windows only: open PowerShell as Administrator, run `wsl --install`, then **restart the PC** before first opening Docker Desktop.
3. **Node.js 20 or newer** (for the frontend): https://nodejs.org (choose LTS)

Check they work:

```powershell
git --version
docker --version
node -v
```

`node -v` must print `v20` or higher.

**Optional but recommended (Windows, stops Docker from eating all your RAM).**
Create the file `C:\Users\<your-name>\.wslconfig` with:

```ini
[wsl2]
memory=4GB
processors=2
swap=2GB
```

Then run `wsl --shutdown` once.

---

## 2. Get the code

```powershell
git clone https://github.com/Vishesh-1204/CAPSTONE_FINANCE_ADVISOR.git
cd CAPSTONE_FINANCE_ADVISOR
git checkout DEV
git pull origin DEV
```

The branch name is **`DEV`** in capitals. All the commands below run from this folder (the repo root, the one containing `docker-compose.yml`).

---

## 3. Create your settings files

### Backend `.env`

```powershell
copy backend\.env.example backend\.env
```
Mac/Linux: `cp backend/.env.example backend/.env`

Open `backend\.env` and set your free Gemini key (get one at https://aistudio.google.com, no card needed):

```text
LLM_PROVIDER=gemini
GEMINI_API_KEY=paste-your-key-here
```

Everything else can stay as it is. The app still works without a key: transactions are categorized by rules, and budget and forecast text falls back to templates.

**Never commit `.env` or share your key.** It is already in `.gitignore`.

### Frontend `.env.local`

```powershell
copy frontend\.env.local.example frontend\.env.local
```
Mac/Linux: `cp frontend/.env.local.example frontend/.env.local`

If the example file does not exist, create `frontend\.env.local` containing exactly:

```text
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

---

## 4. Start the backend and database (Terminal 1)

1. Open **Docker Desktop** and wait until it says **Engine running**.
2. In the repo root:

```powershell
docker compose up --build
```

- The first build can take several minutes (it downloads packages). Later starts are fast.
- Success looks like: `Application startup complete.`
- The database tables are created automatically (migrations run on every start).
- **Leave this terminal open.**

Check it: open http://127.0.0.1:8000/health. You should see `{"status":"ok", ...}`.

---

## 5. Start the frontend (Terminal 2)

Open a second terminal (in VS Code: the `+` in the terminal panel), then:

```powershell
cd frontend
npm install
npm run dev
```

- `npm install` is needed only the first time (and when `package.json` changes).
- Open **http://localhost:3000**.
- **Leave this terminal open too.**

---

## 6. Try the app (2 minutes)

1. Open http://localhost:3000 and **register**, then log in.
2. Make a small fake statement. In a third terminal, in the repo root:

```powershell
@"
Date,Description,Debit,Credit
01/10/2026,UPI/ZOMATO/4821/PAYMENT,450.00,
02/10/2026,UPI/UBER INDIA/9921/RIDE,230.00,
03/10/2026,NETFLIX.COM,649.00,
04/10/2026,SALARY OCT ACME PVT LTD,,50000.00
05/10/2026,UPI/BIGBASKET/1123,1280.50,
06/10/2026,ELECTRICITY BILL BESCOM,1890.00,
"@ | Set-Content sample_transactions.csv
```

3. In the app: **Upload** the file, then run **Categorize**, then open **Transactions**.
4. Open **Budgets**: save your income and savings target, then **Generate budget**.

Use fake data only. Never upload a real bank statement while using the free Gemini key (Google may use free-tier inputs to improve its products).

Prefer the raw API instead of the frontend? Use Swagger at http://127.0.0.1:8000/docs: register, log in, click **Authorize**, then try the endpoints.

---

## 7. Check that everything works (one command each)

Run these while Terminal 1 (Docker) is running:

```powershell
docker compose exec backend python -m pytest tests -q
docker compose exec backend python -m scripts.smoke_test
```

- The first runs all automated tests. It must end with `N passed` and no failures.
- The second runs a live end-to-end check against the running API. It must end with `ALL ... STEPS PASSED`.

---

## 8. Every day after that

```powershell
# Terminal 1 (repo root), after Docker Desktop shows "Engine running"
docker compose up

# Terminal 2
cd frontend
npm run dev
```

Use `docker compose up --build` only after `requirements.txt` or the `Dockerfile` changed.

To stop: press `Ctrl+C` in each terminal. Optionally run `docker compose down` (this keeps your data).

---

## 9. Reset the database (deletes all test data)

```powershell
docker compose down -v
docker compose up --build
```

`-v` deletes the database volume. Use it only when you want a completely fresh database. You will need to register again.

---

## 10. Problems and fixes

| Problem | Fix |
|---|---|
| `failed to connect to the docker API` | Docker Desktop is not running yet. Open it and wait for "Engine running". |
| `env file ...\backend\.env not found` | Do step 3 (`copy backend\.env.example backend\.env`). |
| `port is already allocated` (8000 or 5432) | `docker compose down`, then `docker compose up`. If it persists, restart the PC. |
| `docker compose` says no configuration file | You are in the wrong folder. `cd` to the repo root (where `docker-compose.yml` is). |
| Build fails with `Could not find a version ... Mako` or timeouts | Network was slow or dropped. Run `docker compose up --build` again. Try another network or a phone hotspot. |
| Build is extremely slow (kB/s) | PyPI download is slow on your network. Wait, or switch network. It only happens on the first build. |
| Docker Desktop error mentioning `daemon.json` / `\x00` | Close Docker Desktop, run `wsl --shutdown`, rename the folder `C:\Users\<you>\.docker` to `.docker.bad`, start Docker Desktop again. |
| Laptop freezes when Docker starts | Create `.wslconfig` as in step 1, then `wsl --shutdown`. |
| `localhost:3000` does not load | Terminal 2 (`npm run dev`) is not running, or you opened port 8000 by mistake. |
| Frontend loads but shows a backend/connection error | Terminal 1 is not running, or `frontend\.env.local` is missing or wrong. |
| `npm install` errors | Check `node -v` is 20 or newer. Delete `frontend\node_modules` and run `npm install` again. |
| Gemini errors / rate limit messages | Check `GEMINI_API_KEY` in `backend\.env`, then `docker compose restart backend`. Free tier allows only a few requests per minute. |
| 401 Unauthorized in Swagger | Your login token expired. Log in again and click **Authorize**. |
| `git pull` says local changes would be overwritten | `git stash`, then `git pull origin DEV`, then `git stash pop`. |

---

## 11. Git workflow (teammates)

```powershell
git checkout DEV
git pull origin DEV
git checkout -b feature/<your-name>-<short-description>
# ... make changes ...
git status                      # make sure .env is NOT listed
git add .
git commit -m "clear description of what you did"
git push -u origin feature/<your-name>-<short-description>
```

Then open a **Pull Request into `DEV`** on GitHub. Never push directly to `main` or `DEV`. Never commit `.env`, real bank statements, or API keys.

---

## 12. Project map

```text
backend/    FastAPI app (app/), database migrations (migrations/), tests (tests/), scripts/, evaluation/
frontend/   Next.js test console
docs/       Specs and per-module documentation
CLAUDE.md   Project notes read by Claude Code (keep it up to date)
```

More detail: `docs/README.md` (architecture, API, database, testing) and `docs/testing.md` (manual test checklist).