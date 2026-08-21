# app/core/config.py

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Centralized application settings.
    Pydantic automatically reads matching values from the .env file
    and validates their types (e.g. PROJECT_NAME must be a string).
    """

    # General app info
    PROJECT_NAME: str = "Agentic AI Personal Finance Advisor"
    ENVIRONMENT: str = "development"  # development | staging | production

    # Database connection string, e.g.
    # postgresql://user:password@localhost:5432/finance_db
    DATABASE_URL: str

    # Will be used starting Phase 2 for JWT auth
    SECRET_KEY: str = "changeme"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# Singleton instance — import this everywhere instead of re-reading .env
settings = Settings()