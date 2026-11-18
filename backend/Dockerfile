FROM python:3.11-slim

WORKDIR /app

# LightGBM's Linux wheel needs the OpenMP runtime (libgomp), which the slim image does not include
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (better Docker layer caching).
# INSTALL_DEV=true (set by docker-compose.yml for local development) also
# installs test tools and the fixture generator; a production build leaves it
# false so none of that ships.
ARG INSTALL_DEV=false
COPY requirements.txt requirements-dev.txt ./
# Be patient on slow or flaky networks: retry failed downloads and wait longer
ENV PIP_DEFAULT_TIMEOUT=100 \
    PIP_RETRIES=10
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install --no-cache-dir -r requirements-dev.txt; fi

# Optional FinBERT extras (torch CPU + transformers), only for the "ml" Compose profile.
# A SEPARATE, later layer on purpose: the default image (INSTALL_ML=false) keeps every cached
# layer above, and this step is a no-op for it.
ARG INSTALL_ML=false
COPY requirements-ml.txt ./
RUN if [ "$INSTALL_ML" = "true" ]; then pip install --no-cache-dir -r requirements-ml.txt; fi

# Copy application code
COPY . .

EXPOSE 8000

# Apply database migrations first, then start the API. `exec` makes uvicorn
# the main process so Ctrl+C / docker stop reach it directly. If a migration
# fails, && stops here and the container exits with the error visible in the logs.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]