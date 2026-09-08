========================================================================
 Agentic AI-Based Autonomous Personal Finance Advisor
 SETUP GUIDE FOR TEAM MEMBERS (Priti, Anvisha, Dev)
 Phase 1 - Project Setup Verification
========================================================================

This guide gets the backend + database running on your laptop using
Docker. You do NOT need to install Python, PostgreSQL, or any
dependencies manually — Docker handles all of that for you.

Follow the steps in order. Do not skip steps.

------------------------------------------------------------------------
STEP 0: PREREQUISITES (one-time setup)
------------------------------------------------------------------------

1. Install Git
   Download: https://git-scm.com/downloads

2. Install Docker Desktop
   Download: https://www.docker.com/products/docker-desktop/

   Windows users: Docker Desktop requires WSL 2.
   Open PowerShell as Administrator and run:
       wsl --install
   Then RESTART your PC before opening Docker Desktop for the first time.

3. Verify Docker is installed correctly.
   Open a terminal (PowerShell / Terminal / Bash) and run:

       docker --version
       docker run hello-world

   You should see a "Hello from Docker!" message.
   If you see this, Docker is working. If not, do not proceed —
   fix Docker first (search the exact error message you see).

------------------------------------------------------------------------
STEP 1: CLONE THE REPOSITORY
------------------------------------------------------------------------

Open a terminal, navigate to the folder where you want the project,
then run:

    git clone https://github.com/<your-username>/finance-advisor.git
    cd finance-advisor
    git checkout dev
    git pull

------------------------------------------------------------------------
STEP 2: CREATE YOUR OWN .env FILE
------------------------------------------------------------------------

Each person needs their own local .env file (this file is never
pushed to GitHub — it's private to your machine).

    cd backend
    copy .env.example .env        (Windows PowerShell / CMD)
    cp .env.example .env          (macOS / Linux)

    cd ..

You do not need to edit the values inside .env for now — the defaults
already match docker-compose.yml.

------------------------------------------------------------------------
STEP 3: BUILD AND START THE PROJECT
------------------------------------------------------------------------

From the ROOT of the repo (the "finance-advisor" folder, NOT the
"backend" folder), run:

    docker compose up --build

Wait for the logs to settle. You should see a line similar to:

    backend-1  | INFO:     Uvicorn running on http://0.0.0.0:8000

Leave this terminal window open — it's running your server.
Do not close it while testing.

------------------------------------------------------------------------
STEP 4: TEST THE BACKEND IS ALIVE
------------------------------------------------------------------------

Open a web browser and go to:

    http://127.0.0.1:8000/health

Expected result (exact text may vary slightly):

    {"status":"ok","project":"Agentic AI Personal Finance Advisor"}

If you see this JSON response, your backend is working correctly.

------------------------------------------------------------------------
STEP 5: TEST THE DATABASE IS ALIVE (optional but recommended)
------------------------------------------------------------------------

Open a SECOND terminal window (keep the first one running from Step 3)
and run:

    docker ps

Look for a container name ending in "-db-1" (e.g. finance-advisor-db-1).
Copy that exact name, then run:

    docker exec -it finance-advisor-db-1 psql -U finance_user -d finance_db -c "\dt"

Expected result:

    Did not find any relations.

This is CORRECT — it means PostgreSQL is running but has no tables
yet (tables get added starting Phase 2).

------------------------------------------------------------------------
STEP 6: STOPPING THE PROJECT
------------------------------------------------------------------------

When you're done working, go back to the terminal running
"docker compose up" and press:

    CTRL + C

To fully stop and remove the containers, run:

    docker compose down

Your database data is preserved between sessions (it's stored in a
Docker volume), so you won't lose anything by stopping it.

------------------------------------------------------------------------
COMMON PROBLEMS AND FIXES
------------------------------------------------------------------------

Problem: "port is already allocated" error on 8000 or 5432
Fix:     Something else on your laptop is already using that port.
         Close other apps using those ports, or restart your computer.

Problem: "WSL 2 installation is incomplete" (Windows only)
Fix:     Run "wsl --install" in PowerShell (as Administrator), then
         restart your PC before opening Docker Desktop again.

Problem: docker compose up gives a build error
Fix:     Make sure you are running the command from the REPO ROOT
         folder (finance-advisor), not from inside "backend".

Problem: .env file not found / DATABASE_URL missing
Fix:     Make sure you completed Step 2 — copy .env.example to .env
         inside the "backend" folder.

Problem: Docker Desktop won't start at all
Fix:     Confirm virtualization is enabled in your BIOS settings, and
         that you restarted your PC after installing WSL 2.

------------------------------------------------------------------------
DAILY WORKFLOW ONCE SETUP IS DONE
------------------------------------------------------------------------

Every time you sit down to work on the project:

    cd finance-advisor
    git checkout dev
    git pull
    docker compose up --build

Before starting your own feature work:

    git checkout -b feature/<your-name>-<short-description>
    (example: git checkout -b feature/auth-priti)

When your work is ready:

    git add .
    git commit -m "clear description of what you did"
    git push -u origin feature/<your-name>-<short-description>

Then open a Pull Request into the "dev" branch on GitHub.
Do NOT push directly to "main" or "dev".

========================================================================
 If your /health check and database check both succeed, Phase 1 is
 verified on your machine. Message the team lead (Vishesh) to confirm.
========================================================================