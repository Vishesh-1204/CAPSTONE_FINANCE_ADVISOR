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

        # --- LLM configuration ---
    # Which provider the factory should build: "gemini" | "openai" | "huggingface"
    LLM_PROVIDER: str = "gemini"

    # Lower temperature = more deterministic output.
    # Categorization is a classification task, not creative writing,
    # so we want consistency, not variety.
    LLM_TEMPERATURE: float = 0.0

    # Gemini (free tier via Google AI Studio)
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash-lite"

    # OpenAI (alternative provider)
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o-mini"

    # --- Expense categorizer (Phase 3B v2) ---
    # Ablation switches: turn a layer off to measure how much it contributes.
    CATEGORIZER_ENABLE_MEMORY: bool = True    # reuse the user's past corrections
    CATEGORIZER_ENABLE_RULES: bool = True     # keyword rules before calling the LLM
    # Descriptions sent to the LLM per request, and the pause between requests
    # (free-tier Gemini allows only ~10-15 requests/minute).
    CATEGORIZER_BATCH_SIZE: int = 20
    CATEGORIZER_BATCH_DELAY_SECONDS: float = 4.0
    # Results below this confidence are flagged needs_review for a human.
    CATEGORIZER_REVIEW_THRESHOLD: float = 0.60
    # Max past user corrections included in the prompt as few-shot examples.
    CATEGORIZER_FEW_SHOT_LIMIT: int = 10

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# Singleton instance — import this everywhere instead of re-reading .env
settings = Settings()