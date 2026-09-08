# app/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging_config import configure_logging
import logging

# Set up logging before anything else runs
configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.PROJECT_NAME)

# Allow the Next.js frontend (running on a different port) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Next.js dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    logger.info(f"Starting {settings.PROJECT_NAME} in {settings.ENVIRONMENT} mode")


@app.get("/health")
def health_check() -> dict:
    """
    Simple endpoint to confirm the API is alive.
    Used by Docker and, later, deployment health checks.
    """
    return {"status": "ok", "project": settings.PROJECT_NAME}