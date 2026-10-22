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

    # Secondary provider tried when the primary fails (quota, outage). Empty = no fallback.
    # If both fail, rows stay uncategorized with a clear message (never labelled "Other").
    LLM_FALLBACK_PROVIDER: str | None = None

    # Comma-separated browser origins allowed to call the API (the Next.js dev server)
    CORS_ORIGINS: str = "http://localhost:3000"

    # --- Expense categorizer (Phase 3B v2) ---
    # Ablation switches: turn a layer off to measure how much it contributes.
    CATEGORIZER_ENABLE_MEMORY: bool = True    # reuse the user's past corrections
    CATEGORIZER_ENABLE_RULES: bool = True     # keyword rules before calling the LLM
    CATEGORIZER_ENABLE_PAYEE_RULES: bool = True   # the user's "remember this payee" rules
    # Payments that look person-to-person are labelled Transfers locally and are
    # NEVER sent to the LLM. Turn off only for the privacy-leakage ablation.
    CATEGORIZER_ENABLE_PERSON_GUARD: bool = True
    # Descriptions sent to the LLM per request, and the pause between requests
    # (free-tier Gemini allows only ~10-15 requests/minute).
    CATEGORIZER_BATCH_SIZE: int = 20
    CATEGORIZER_BATCH_DELAY_SECONDS: float = 4.0
    # Results below this confidence are flagged needs_review for a human.
    CATEGORIZER_REVIEW_THRESHOLD: float = 0.60
    # Max past user corrections included in the prompt as few-shot examples.
    CATEGORIZER_FEW_SHOT_LIMIT: int = 10

    # --- Budget Planning Agent (Phase 4) ---
    # Full months of history (before the target month) the planner averages over.
    BUDGET_LOOKBACK_MONTHS: int = 3
    # Fraction cut from each discretionary category's average (0.10 = 10%). Essentials are never cut.
    BUDGET_DISCRETIONARY_TRIM: float = 0.10
    # Status turns 'warning' once spending reaches this share of the limit (0.80 = 80%).
    BUDGET_WARNING_THRESHOLD: float = 0.80
    # The explanation guard lets the LLM round a plan number by up to this many rupees.
    BUDGET_GUARD_TOLERANCE: float = 1.0
    # Ablation switch (B1 vs B2): False = template explanations only, no LLM call.
    BUDGET_USE_LLM_EXPLANATIONS: bool = True

    # --- Financial Forecast Agent (Phase 5) ---
    # Target coverage of the prediction interval (0.8 = the true value should fall inside 80% of the time).
    FORECAST_INTERVAL_COVERAGE: float = 0.8
    # Fewer complete months of expenses than this -> HTTP 422 (nothing to forecast from).
    FORECAST_MIN_HISTORY_MONTHS: int = 1
    # A payee is "recurring" after this many roughly monthly payments ...
    FORECAST_RECURRING_MIN_OCCURRENCES: int = 3
    # ... whose recent amounts stay within this fraction of their median (0.15 = 15%).
    FORECAST_RECURRING_AMOUNT_TOLERANCE: float = 0.15
    # A recurring item not seen for this many days before as_of is treated as stopped (not projected).
    FORECAST_RECURRING_STALE_DAYS: int = 45
    # Half-width of the interval (fraction of the point) when the backtest has too few errors.
    FORECAST_FALLBACK_INTERVAL_WIDTH: float = 0.25
    # Backtest folds needed before the agent CHOOSES a model; fewer -> moving average + low_confidence.
    FORECAST_MIN_BACKTEST_FOLDS: int = 2
    # Horizon-sum backtest errors needed for a residual-based interval; fewer -> fallback width.
    FORECAST_MIN_INTERVAL_ERRORS: int = 3
    # Ablation switch: False = template explanations only, no LLM call.
    FORECAST_USE_LLM_EXPLANATIONS: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS_ORIGINS split into a clean list ('a, b' -> ['a', 'b'])."""
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


# Singleton instance — import this everywhere instead of re-reading .env
settings = Settings()