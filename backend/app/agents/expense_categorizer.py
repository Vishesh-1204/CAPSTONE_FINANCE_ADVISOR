# app/agents/expense_categorizer.py

import json
import logging
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.llm.factory import get_llm_provider

logger = logging.getLogger(__name__)


# A fixed taxonomy. Constraining the LLM to a known set of categories is
# essential — without it the model invents new category names ("Takeout",
# "Food Delivery", "Restaurants") and your budget aggregations break.
EXPENSE_CATEGORIES: list[str] = [
    "Food & Dining",
    "Groceries",
    "Transportation",
    "Housing & Rent",
    "Utilities",
    "Healthcare",
    "Entertainment",
    "Shopping",
    "Education",
    "Travel",
    "Insurance",
    "Investments & Savings",
    "Personal Care",
    "Subscriptions",
    "Other",
]


class CategorizationResult(BaseModel):
    """Structured output returned by the categorization agent."""
    category: str = Field(description="One of the predefined expense categories")
    confidence: float = Field(description="Model confidence between 0.0 and 1.0")
    reasoning: str = Field(description="Short explanation of why this category was chosen")


# The prompt. Note the explicit instruction to return ONLY JSON —
# LLMs love adding conversational preamble, which breaks parsing.
CATEGORIZATION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a financial transaction classifier for a personal finance app in India.\n"
        "Classify each expense into EXACTLY ONE of these categories:\n"
        "{categories}\n\n"
        "Rules:\n"
        "- You MUST choose a category from the list above. Never invent new categories.\n"
        "- If genuinely unclear, use 'Other' with a low confidence score.\n"
        "- Respond with ONLY a valid JSON object, no markdown fences, no preamble.\n\n"
        'Format: {{"category": "...", "confidence": 0.0, "reasoning": "..."}}'
    ),
    (
        "human",
        "Expense description: {description}\nAmount: {amount}"
    ),
])


class ExpenseCategorizerAgent:
    """
    The Expense Intelligence Agent.

    Responsible for one thing only: turning a free-text expense
    description into a structured, explainable category assignment.
    It knows nothing about databases or HTTP — that separation is
    what lets us test it in isolation and swap LLM providers freely.
    """

    def __init__(self) -> None:
        provider = get_llm_provider()
        self._provider_name = provider.provider_name

        # LangChain Expression Language (LCEL): prompt -> model -> string parser.
        # The '|' operator pipes each step's output into the next.
        self._chain = CATEGORIZATION_PROMPT | provider.get_chat_model() | StrOutputParser()

    @retry(
        # Free-tier Gemini allows only ~10-15 requests/minute, so 429 rate-limit
        # errors are expected during batch runs. Exponential backoff waits
        # 2s, 4s, 8s between retries rather than hammering the API.
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def _invoke_llm(self, description: str, amount: float) -> str:
        """Calls the LLM with retry/backoff. Returns the raw text response."""
        return self._chain.invoke({
            "categories": ", ".join(EXPENSE_CATEGORIES),
            "description": description,
            "amount": amount,
        })

    def categorize(self, description: str, amount: float) -> CategorizationResult:
        """
        Categorizes a single expense.

        Always returns a valid result — if the LLM fails or returns
        malformed output, we fall back to 'Other' with zero confidence
        rather than crashing the whole import. Graceful degradation
        matters when processing a 200-row CSV.
        """
        try:
            raw_response = self._invoke_llm(description, amount)

            # Defensive cleanup: models often wrap JSON in ```json fences
            # despite being told not to.
            cleaned = raw_response.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

            parsed = json.loads(cleaned)
            result = CategorizationResult(**parsed)

            # Guard against the model ignoring our taxonomy anyway
            if result.category not in EXPENSE_CATEGORIES:
                logger.warning(
                    f"LLM returned unknown category '{result.category}' for '{description}'. Using 'Other'."
                )
                result.category = "Other"
                result.confidence = 0.0

            logger.info(
                f"Categorized '{description}' -> {result.category} "
                f"(confidence={result.confidence}, provider={self._provider_name})"
            )
            return result

        except Exception as exc:
            logger.error(f"Categorization failed for '{description}': {exc}")
            return CategorizationResult(
                category="Other",
                confidence=0.0,
                reasoning=f"Automatic categorization failed: {type(exc).__name__}",
            )
