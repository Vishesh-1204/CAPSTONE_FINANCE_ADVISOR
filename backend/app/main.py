# app/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

from app.core.config import settings
from app.core.logging_config import configure_logging
from app.db.session import engine
from app.db.base_class import Base
from app.api.routes import auth

# Import models so Base knows about them before create_all runs
from app.models import user  # noqa: F401

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.PROJECT_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)


@app.on_event("startup")
async def on_startup() -> None:
    logger.info(f"Starting {settings.PROJECT_NAME} in {settings.ENVIRONMENT} mode")
    # Creates tables if they don't already exist (dev-only convenience)
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables verified/created.")


@app.get("/health")
def health_check() -> dict:
    return {"status": "ok", "project": settings.PROJECT_NAME}