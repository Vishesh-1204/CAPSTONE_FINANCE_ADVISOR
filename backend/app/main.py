# app/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging


from app.core.config import settings
from app.core.logging_config import configure_logging
from app.api.routes import auth, payee_rules, transactions

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.PROJECT_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,     # from CORS_ORIGINS in .env
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(transactions.router)
app.include_router(payee_rules.router)


@app.on_event("startup")
async def on_startup() -> None:
    # The schema is managed by Alembic migrations (`alembic upgrade head` runs
    # before uvicorn in the Docker CMD), so the app no longer calls create_all.
    logger.info(f"Starting {settings.PROJECT_NAME} in {settings.ENVIRONMENT} mode")


@app.get("/health")
def health_check() -> dict:
    return {"status": "ok", "project": settings.PROJECT_NAME}




