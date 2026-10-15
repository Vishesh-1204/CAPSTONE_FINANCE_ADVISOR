FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better Docker layer caching).
# INSTALL_DEV=true (set by docker-compose.yml for local development) also
# installs test tools and the fixture generator; a production build leaves it
# false so none of that ships.
ARG INSTALL_DEV=false
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install --no-cache-dir -r requirements-dev.txt; fi

# Copy application code
COPY . .

EXPOSE 8000

# Apply database migrations first, then start the API. `exec` makes uvicorn
# the main process so Ctrl+C / docker stop reach it directly. If a migration
# fails, && stops here and the container exits with the error visible in the logs.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]