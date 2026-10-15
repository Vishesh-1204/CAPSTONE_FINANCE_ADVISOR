FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

EXPOSE 8000

# Apply database migrations first, then start the API. `exec` makes uvicorn
# the main process so Ctrl+C / docker stop reach it directly. If a migration
# fails, && stops here and the container exits with the error visible in the logs.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]